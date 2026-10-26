from flask import redirect, render_template, request, url_for

from models import Concert, db
from services.retrieval import concert_card


def register_search(app):
    @app.get("/search")
    def search():
        # Preserve bookmarked queries while consolidating the actual interface.
        return redirect(url_for("profile", **request.args.to_dict()))

    @app.get("/concerts/<int:concert_id>")
    def concert_detail(concert_id):
        concert = db.get_or_404(Concert, concert_id)
        return render_template("concert.html", concert=concert, result_events=[concert_card(concert)])
