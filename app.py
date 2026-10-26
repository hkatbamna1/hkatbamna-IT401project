import os

import click
from flask import Flask, jsonify, render_template, request
from flask_migrate import Migrate
from sqlalchemy.exc import SQLAlchemyError

from config import config
from routes.main import register_routes
from routes.auth import register_auth
from routes.profile import register_profile
from routes.search import register_search
from models import db


def create_app(config_name=None, config_overrides=None):
    config_name = config_name or os.environ.get("FLASK_ENV", "default")

    app = Flask(__name__)
    app.config.from_object(config[config_name])
    if config_overrides:
        app.config.update(config_overrides)
    db.init_app(app)
    Migrate(app, db, render_as_batch=True)

    register_routes(app)
    register_auth(app)
    register_profile(app)
    register_search(app)

    @app.cli.command("backfill-saved-concerts")
    def backfill_saved_concerts_command():
        """Index private saved snapshots without changing their content or ownership."""
        from services.catalog import backfill_saved_concerts
        try:
            count = backfill_saved_concerts()
        except SQLAlchemyError:
            db.session.rollback()
            raise click.ClickException("Saved concert indexing failed. Check the database and run db upgrade first.") from None
        click.echo(f"Indexed {count} concerts from private saved searches.")

    @app.cli.command("backfill-catalog")
    def backfill_catalog_command():
        """Import existing cached concerts, without contacting external providers."""
        from services.catalog import backfill_catalog
        try:
            count = backfill_catalog()
        except SQLAlchemyError:
            db.session.rollback()
            raise click.ClickException("Catalog import failed. Check the database and run db upgrade first.") from None
        click.echo(f"Catalog ready: {count} concert records added or updated.")

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
        message = "Search is temporarily unavailable. Please try again shortly."
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
