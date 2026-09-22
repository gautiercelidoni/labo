from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect

from app.models.base import Base
from app.security.tenancy import TenantSession

db = SQLAlchemy(model_class=Base, session_options={"class_": TenantSession})
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()

login_manager.login_view = "auth.login"
login_manager.login_message = "Veuillez vous connecter pour accéder à cette page."
login_manager.login_message_category = "info"
login_manager.session_protection = "strong"
