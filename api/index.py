import sys
import os

# Permite que o Python encontre o seu app.py na raiz do projeto
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app