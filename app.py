import hmac
import os
import re
import secrets
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
from flask import Flask, jsonify, render_template, request, session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
PHONE_PATTERN = re.compile(r"^\(\d{2}\)\s9?\d{4}-\d{4}$")
NAME_PATTERN = re.compile(r"^[A-Za-zÀ-ÿ' -]{3,120}$")
IMAGE_FILES = {
    "heroPizza": "caprichosa-hero.jpg",
    "pizzaMargherita": "caprichosa-margherita.jpg",
    "pizzaCalabresa": "caprichosa-calabresa.jpg",
    "pizzaCaprese": "caprichosa-caprese.jpg",
    "pizzaCogumelos": "caprichosa-cogumelos.jpg",
    "storyIngredients": "caprichosa-ingredients.jpg",
}


def load_local_env():
    """Carrega pares simples KEY=VALUE de um .env sem substituir variáveis de produção."""
    env_file = Path(__file__).with_name(".env")
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_local_env()


class SupabaseError(Exception):
    """Erro seguro para indisponibilidade ou configuração do Supabase."""


def environment(name, required=True):
    value = os.environ.get(name, "").strip()
    if required and not value:
        raise SupabaseError(f"A variável {name} não está configurada no ambiente.")
    return value


def supabase_headers(extra=None):
    secret = environment("SUPABASE_SECRET_KEY")
    headers = {
        "apikey": secret,
        "Authorization": f"Bearer {secret}",
        "Content-Type": "application/json",
    }
    headers.update(extra or {})
    return headers


def supabase_rest(method, resource, *, payload=None, extra_headers=None):
    base_url = environment("SUPABASE_URL").rstrip("/")
    try:
        response = requests.request(
            method,
            f"{base_url}/rest/v1/{resource}",
            headers=supabase_headers(extra_headers),
            json=payload,
            timeout=12,
        )
    except requests.RequestException as error:
        raise SupabaseError("Não foi possível acessar o banco de reservas no momento.") from error
    if not response.ok:
        raise SupabaseError("Não foi possível concluir a operação no banco de reservas.")
    if not response.content:
        return None
    return response.json()


def as_time(value):
    if not isinstance(value, str) or not TIME_PATTERN.fullmatch(value):
        return None
    return datetime.strptime(value, "%H:%M").time()


def public_image_urls():
    base_url = environment("SUPABASE_URL").rstrip("/")
    bucket = environment("SUPABASE_IMAGE_BUCKET", required=False) or "pizzas_imgs"
    prefix = f"{base_url}/storage/v1/object/public/{quote(bucket, safe='')}"
    return {name: f"{prefix}/{quote(filename, safe='/')}" for name, filename in IMAGE_FILES.items()}


def get_settings():
    rows = supabase_rest("GET", "availability_settings?select=start_time,end_time,slot_minutes,daily_reservation_limit&id=eq.1&limit=1")
    if not rows:
        raise SupabaseError("A configuração de horários ainda não foi criada no Supabase.")
    row = rows[0]
    return {
        "startTime": row["start_time"],
        "endTime": row["end_time"],
        "slotMinutes": row["slot_minutes"],
        "dailyReservationLimit": row["daily_reservation_limit"],
    }


def get_blocked_dates():
    today = date.today().isoformat()
    rows = supabase_rest(
        "GET",
        f"blocked_dates?select=blocked_date,reason&blocked_date=gte.{quote(today)}&order=blocked_date.asc",
    )
    return [{"blockedDate": row["blocked_date"], "reason": row.get("reason")} for row in rows]


def active_reservations_for_day(booking_date):
    rows = supabase_rest(
        "GET",
        f"reservations?select=id&booking_date=eq.{quote(booking_date.isoformat())}&status=in.(pending,confirmed)&limit=500",
    )
    return len(rows)


def current_week_overview():
    settings = get_settings()
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    rows = supabase_rest(
        "GET",
        "reservations?select=booking_date,party_size,status"
        f"&booking_date=gte.{quote(week_start.isoformat())}&booking_date=lte.{quote(week_end.isoformat())}&limit=500",
    )
    active_statuses = {"pending", "confirmed"}
    by_day = {}
    for row in rows:
        if row["status"] not in active_statuses:
            continue
        day = by_day.setdefault(row["booking_date"], {"reservations": 0, "people": 0})
        day["reservations"] += 1
        day["people"] += int(row["party_size"])

    limit = int(settings["dailyReservationLimit"])
    days = []
    for offset in range(7):
        current = week_start + timedelta(days=offset)
        values = by_day.get(current.isoformat(), {"reservations": 0, "people": 0})
        days.append({
            "date": current.isoformat(),
            "label": current.strftime("%a").capitalize(),
            "reservations": values["reservations"],
            "people": values["people"],
            "remaining": max(0, limit - values["reservations"]),
        })
    return {
        "weekStart": week_start.isoformat(),
        "weekEnd": week_end.isoformat(),
        "dailyReservationLimit": limit,
        "totalReservations": sum(item["reservations"] for item in days),
        "totalPeople": sum(item["people"] for item in days),
        "days": days,
    }


def normalize_reservation(payload, settings):
    if not isinstance(payload, dict):
        return {"form": "Envie os dados da reserva no formato esperado."}, None
    errors = {}
    name = " ".join(str(payload.get("name", "")).strip().split())
    phone = str(payload.get("phone", "")).strip()
    booking_date = str(payload.get("date", "")).strip()
    booking_time = str(payload.get("time", "")).strip()
    party_size = payload.get("partySize")

    if not NAME_PATTERN.fullmatch(name):
        errors["name"] = "Informe seu nome completo usando pelo menos 3 letras."
    if not PHONE_PATTERN.fullmatch(phone):
        errors["phone"] = "Informe um WhatsApp válido, incluindo DDD."
    try:
        parsed_date = datetime.strptime(booking_date, "%Y-%m-%d").date()
        if not date.today() <= parsed_date <= date.today() + timedelta(days=14):
            errors["date"] = "Escolha uma data entre hoje e os próximos 14 dias."
    except ValueError:
        errors["date"] = "Escolha uma data válida."
        parsed_date = None
    try:
        normalized_size = int(party_size)
        if not 1 <= normalized_size <= 20:
            errors["partySize"] = "A reserva deve ser para 1 a 20 pessoas."
    except (TypeError, ValueError):
        errors["partySize"] = "Selecione a quantidade de pessoas."
        normalized_size = None

    selected_time = as_time(booking_time)
    start_time, end_time = as_time(settings["startTime"]), as_time(settings["endTime"])
    if not selected_time or not start_time or not end_time or not start_time <= selected_time <= end_time:
        errors["time"] = "Escolha um horário dentro da janela de atendimento."
    elif ((selected_time.hour * 60 + selected_time.minute) - (start_time.hour * 60 + start_time.minute)) % int(settings["slotMinutes"]):
        errors["time"] = "Escolha um horário disponível na lista."

    if parsed_date and not errors.get("date"):
        blocked = supabase_rest("GET", f"blocked_dates?select=blocked_date&blocked_date=eq.{quote(parsed_date.isoformat())}&limit=1")
        if blocked:
            errors["date"] = "Esta data está indisponível para reservas."
    return errors, {"name": name, "phone": phone, "date": parsed_date, "time": booking_time, "partySize": normalized_size}


def cleanup_expired_reservations():
    """Remove automaticamente reservas com mais de 1 hora de atraso/conclusão."""
    cutoff = datetime.now() - timedelta(hours=1)
    cutoff_date = cutoff.date().isoformat()
    cutoff_time = cutoff.strftime("%H:%M")

    try:
        # 1. Apaga reservas de dias anteriores a hoje
        supabase_rest(
            "DELETE",
            f"reservations?booking_date=lt.{quote(cutoff_date)}",
            extra_headers={"Prefer": "return=minimal"}
        )
        # 2. Apaga reservas de hoje que têm horário menor que (agora - 1h)
        supabase_rest(
            "DELETE",
            f"reservations?booking_date=eq.{quote(cutoff_date)}&booking_time=lt.{quote(cutoff_time)}",
            extra_headers={"Prefer": "return=minimal"}
        )
    except Exception:
        # Se houver alguma oscilação no banco, ignora a limpeza para não travar a tela
        pass


app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("FLASK_SECRET_KEY", os.environ.get("JWT_SECRET", "")),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("FLASK_ENV") == "production",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    MAX_CONTENT_LENGTH=16 * 1024,
)
limiter = Limiter(key_func=get_remote_address, app=app, default_limits=["120 per minute"], storage_uri="memory://")


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' https: data:; style-src 'self'; script-src 'self'; connect-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.errorhandler(SupabaseError)
def supabase_failure(error):
    return jsonify(error=str(error)), 503


@app.errorhandler(413)
def payload_too_large(_error):
    return jsonify(error="A solicitação excede o tamanho permitido."), 413


@app.errorhandler(429)
def rate_limited(_error):
    return jsonify(error="Muitas tentativas. Aguarde alguns minutos e tente novamente."), 429


def valid_same_origin():
    origin = request.headers.get("Origin")
    if not origin:
        return True
    parsed = urlparse(origin)
    return parsed.netloc == request.host and parsed.scheme in {"http", "https"}


def require_admin(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            return jsonify(error="Autenticação administrativa necessária."), 401
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            csrf_token = request.headers.get("X-CSRF-Token", "")
            if not valid_same_origin() or not hmac.compare_digest(csrf_token, session.get("csrf_token", "")):
                return jsonify(error="Não foi possível validar a solicitação."), 403
        return view(*args, **kwargs)
    return wrapped


@app.get("/")
@app.get("/admin")
def home():
    return render_template("index.html", images=public_image_urls())


@app.get("/api/site-config")
def site_config():
    return jsonify(images=public_image_urls())


@app.get("/api/availability")
def availability():
    cleanup_expired_reservations()
    return jsonify(settings=get_settings(), blockedDates=get_blocked_dates())


@app.post("/api/reservations")
@limiter.limit("8 per minute")
def create_reservation():
    settings = get_settings()
    fields, booking = normalize_reservation(request.get_json(silent=True), settings)
    if fields:
        return jsonify(error="Revise os campos destacados.", fields=fields), 400
    if active_reservations_for_day(booking["date"]) >= int(settings["dailyReservationLimit"]):
        return jsonify(
            error="O limite de reservas desta data foi atingido.",
            fields={"date": "Esta data já atingiu o limite de reservas."},
        ), 409
    supabase_rest("POST", "reservations", payload={
        "customer_name": booking["name"], "phone": booking["phone"], "booking_date": booking["date"].isoformat(),
        "booking_time": booking["time"], "party_size": booking["partySize"], "status": "pending",
    }, extra_headers={"Prefer": "return=minimal"})
    return jsonify(message="Sua solicitação de reserva foi registrada.", reservationDate=booking["date"].isoformat()), 201


@app.post("/api/admin/login")
@limiter.limit("5 per 15 minutes")
def admin_login():
    if not valid_same_origin():
        return jsonify(error="Origem não permitida."), 403
    payload = request.get_json(silent=True) or {}
    username, password = str(payload.get("username", "")), str(payload.get("password", ""))
    expected_user, expected_password = environment("ADMIN_USERNAME"), environment("ADMIN_PASSWORD")
    if not (hmac.compare_digest(username, expected_user) and hmac.compare_digest(password, expected_password)):
        return jsonify(error="Usuário ou senha incorretos."), 401
    session.clear(); session.permanent = True; session["is_admin"] = True; session["csrf_token"] = secrets.token_urlsafe(32)
    return jsonify(authenticated=True, csrfToken=session["csrf_token"])


@app.get("/api/admin/session")
def admin_session():
    return jsonify(authenticated=bool(session.get("is_admin")), csrfToken=session.get("csrf_token"))


@app.post("/api/admin/logout")
@require_admin
def admin_logout():
    session.clear()
    return jsonify(success=True)


@app.get("/api/admin/reservations")
@require_admin
def admin_reservations():
    cleanup_expired_reservations()
    rows = supabase_rest("GET", "reservations?select=id,customer_name,phone,booking_date,booking_time,party_size,status,created_at&order=booking_date.asc,booking_time.asc,id.desc&limit=250")
    reservations = [{"id": row["id"], "customerName": row["customer_name"], "phone": row["phone"], "bookingDate": row["booking_date"], "bookingTime": row["booking_time"], "partySize": row["party_size"], "status": row["status"], "createdAt": row["created_at"]} for row in rows]
    return jsonify(reservations=reservations)


@app.get("/api/admin/week")
@require_admin
def admin_week():
    return jsonify(week=current_week_overview())


@app.get("/api/admin/blocked-dates")
@require_admin
def admin_blocked_dates():
    return jsonify(blockedDates=get_blocked_dates())


@app.post("/api/admin/blocked-dates")
@require_admin
def block_date():
    payload = request.get_json(silent=True) or {}
    try:
        blocked_date = datetime.strptime(str(payload.get("date", "")), "%Y-%m-%d").date()
    except ValueError:
        return jsonify(error="Informe uma data válida para bloqueio."), 400
    if blocked_date < date.today():
        return jsonify(error="Não é possível bloquear uma data passada."), 400
    reason = " ".join(str(payload.get("reason", "")).split())[:160] or None
    supabase_rest("POST", "blocked_dates?on_conflict=blocked_date", payload={"blocked_date": blocked_date.isoformat(), "reason": reason}, extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
    return jsonify(message="Data bloqueada com sucesso."), 201


@app.delete("/api/admin/blocked-dates/<blocked_date>")
@require_admin
def unblock_date(blocked_date):
    try:
        normalized = datetime.strptime(blocked_date, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return jsonify(error="Data inválida."), 400
    supabase_rest("DELETE", f"blocked_dates?blocked_date=eq.{quote(normalized)}", extra_headers={"Prefer": "return=minimal"})
    return jsonify(success=True)


@app.get("/api/admin/settings")
@require_admin
def admin_settings():
    return jsonify(settings=get_settings())


@app.put("/api/admin/settings")
@require_admin
def update_settings():
    payload = request.get_json(silent=True) or {}
    start_time, end_time = str(payload.get("startTime", "")), str(payload.get("endTime", ""))
    try: slot_minutes = int(payload.get("slotMinutes", 30))
    except (TypeError, ValueError): slot_minutes = 0
    try: daily_limit = int(payload.get("dailyReservationLimit", 40))
    except (TypeError, ValueError): daily_limit = 0
    if not as_time(start_time) or not as_time(end_time) or as_time(start_time) >= as_time(end_time):
        return jsonify(error="Defina uma janela de horários válida."), 400
    if slot_minutes not in {15, 30, 60}:
        return jsonify(error="O intervalo deve ser de 15, 30 ou 60 minutos."), 400
    if not 1 <= daily_limit <= 250:
        return jsonify(error="O limite diário deve ficar entre 1 e 250 reservas."), 400
    supabase_rest(
        "PATCH",
        "availability_settings?id=eq.1",
        payload={
            "start_time": start_time,
            "end_time": end_time,
            "slot_minutes": slot_minutes,
            "daily_reservation_limit": daily_limit,
        },
        extra_headers={"Prefer": "return=minimal"},
    )
    return jsonify(message="Horários de reserva atualizados.", settings=get_settings())


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False)
