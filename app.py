import os

import click
from flask import Flask, jsonify, render_template, request
from sqlalchemy.exc import SQLAlchemyError

from config import config
from routes.main import register_routes
from routes.auth import register_auth
from routes.profile import register_profile
from models import db


def create_app(config_name=None, config_overrides=None):
    config_name = config_name or os.environ.get("FLASK_ENV", "default")

    app = Flask(__name__)
    app.config.from_object(config[config_name])
    if config_overrides:
        app.config.update(config_overrides)
    db.init_app(app)

    register_routes(app)
    register_auth(app)
    register_profile(app)

    @app.cli.command("init-db")
    def init_db():
        """Create missing Encore tables; never drop or replace existing tables."""
        db.create_all()
        click.echo("Encore database tables are ready.")

    @app.errorhandler(SQLAlchemyError)
    def database_error(error):
        db.session.rollback()
        # Avoid logging SQL parameters, credentials, or private saved notes.
        app.logger.error("Database operation failed (%s)", type(error).__name__)
        message = "Saved searches are temporarily unavailable. Please try again shortly."
        if request.path.startswith("/api/"):
            return jsonify(error=message), 503
        return render_template("error.html", message=message), 503

    @app.errorhandler(400)
    @app.errorhandler(404)
    def input_error(error):
        if request.path.startswith("/api/"):
            return jsonify(error=error.description), error.code
        return render_template("error.html", message=error.description), error.code

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=app.config.get("DEBUG", False))
