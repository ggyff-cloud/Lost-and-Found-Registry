"""Web layer of the Lost & Found Claim Registry.

The routes are thin: they open a connection, call one function in
:mod:`claim_service`, and translate the result into a status code. No route
holds business state, and no route decides whether an item is available, so the
rules stay in one place and the same code is exercised by the web tests and the
benchmark script.

Both an HTML flow (what the demonstration uses) and a JSON API (what the tests
and the benchmark use) are exposed over the same service functions.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from flask import Flask, flash, g, jsonify, redirect, render_template, request, url_for

import claim_service as svc
import db as database

USERS = {
    "u001": "Reporter (u001)",
    "u002": "Claimant (u002)",
    "u003": "Claimant (u003)",
    "u900": "Coordinator (u900)",
}

ITEM_STATES = ("Unclaimed", "Claimed", "Returned")


def create_app(db_path: str | None = None) -> Flask:
    """Build the Flask application.

    Args:
        db_path: Database file to use; a temporary file per app in tests.

    Returns:
        A configured application with the schema and seed rows in place.
    """
    app = Flask(__name__)
    app.config["DATABASE"] = db_path or str(database.DEFAULT_DB)
    app.config["SECRET_KEY"] = "demo-only-not-a-secret"

    with database.connect(app.config["DATABASE"]) as bootstrap:
        database.init_db(bootstrap)
        database.seed(bootstrap)

    def get_conn() -> sqlite3.Connection:
        """Return the per-request connection, opening it on first use."""
        if "conn" not in g:
            g.conn = database.connect(app.config["DATABASE"])
        return g.conn

    @app.teardown_appcontext
    def close_conn(_exc: BaseException | None) -> None:
        """Close the request connection so no transaction outlives the request."""
        conn = g.pop("conn", None)
        if conn is not None:
            conn.close()

    @app.get("/health")
    def health() -> Any:
        """Report readiness; used by the benchmark and by the demo."""
        return jsonify(status="ok", database=app.config["DATABASE"])

    @app.get("/")
    def index() -> Any:
        """Show the item list; the unclaimed view is the default one, which is B2."""
        status = request.args.get("status", "Unclaimed")
        if status not in ITEM_STATES:
            status = "Unclaimed"
        conn = get_conn()
        return render_template(
            "index.html",
            items=svc.list_items(conn, status),
            status=status,
            states=ITEM_STATES,
            counts={state: len(svc.list_items(conn, state)) for state in ITEM_STATES},
            users=USERS,
        )

    @app.route("/register", methods=["GET", "POST"])
    def register() -> Any:
        """Record a lost item, which is backlog item B1."""
        if request.method == "POST":
            try:
                item_id = svc.register_item(
                    get_conn(),
                    name=request.form["name"],
                    place=request.form["place"],
                    occurred_at=request.form["occurred_at"],
                    description=request.form.get("description", ""),
                    reporter_id=request.form.get("reporter_id", "u001"),
                )
            except (svc.ServiceError, KeyError) as exc:
                flash(str(exc) or "name, place and occurred_at are required")
                return redirect(url_for("register"))
            return redirect(url_for("item", item_id=item_id))
        return render_template("register.html", users=USERS)

    @app.get("/items/<int:item_id>")
    def item(item_id: int) -> Any:
        """Show one item and its claim state."""
        try:
            detail = svc.get_item(get_conn(), item_id)
        except svc.NotFound as exc:
            return render_template("error.html", message=str(exc), code=404), 404
        return render_template("item.html", item=detail, users=USERS)

    @app.post("/items/<int:item_id>/claim")
    def claim(item_id: int) -> Any:
        """Claim an item from the HTML form (B3, and the 409 path of B4)."""
        try:
            svc.claim_item(get_conn(), item_id, request.form.get("claimant_id", "u002"))
        except svc.ServiceError as exc:
            flash(str(exc))
            return redirect(url_for("item", item_id=item_id))
        return redirect(url_for("item", item_id=item_id))

    @app.post("/items/<int:item_id>/return")
    def confirm_return(item_id: int) -> Any:
        """Confirm the handover as a coordinator (B5)."""
        try:
            svc.confirm_return(
                get_conn(), item_id, request.form.get("confirmed_by") or None
            )
        except svc.ServiceError as exc:
            flash(str(exc))
        return redirect(url_for("item", item_id=item_id))

    # --- JSON API: same service functions, no rendering ---------------------

    @app.get("/api/items")
    def api_items() -> Any:
        """List unclaimed items as JSON."""
        return jsonify([dict(row) for row in svc.list_unclaimed(get_conn())])

    @app.post("/api/items")
    def api_register() -> Any:
        """Register an item from a JSON body."""
        body = request.get_json(silent=True) or {}
        try:
            item_id = svc.register_item(
                get_conn(),
                name=body.get("name", ""),
                place=body.get("place", ""),
                occurred_at=body.get("occurred_at", ""),
                description=body.get("description", ""),
                reporter_id=body.get("reporter_id", "u001"),
            )
        except svc.ServiceError as exc:
            return jsonify(error=str(exc)), exc.status
        return jsonify(id=item_id), 201

    @app.get("/api/items/<int:item_id>")
    def api_item(item_id: int) -> Any:
        """Return one item with its claim, if any."""
        try:
            return jsonify(svc.get_item(get_conn(), item_id))
        except svc.ServiceError as exc:
            return jsonify(error=str(exc)), exc.status

    @app.post("/api/items/<int:item_id>/claim")
    def api_claim(item_id: int) -> Any:
        """Claim an item; a lost race answers 409 with the same body shape."""
        body = request.get_json(silent=True) or {}
        try:
            stored = svc.claim_item(get_conn(), item_id, body.get("claimant_id", "u002"))
        except svc.ServiceError as exc:
            return jsonify(error=str(exc)), exc.status
        return jsonify(stored), 201

    @app.post("/api/items/<int:item_id>/return")
    def api_return(item_id: int) -> Any:
        """Confirm the handover through the API; a claimant is refused with 403."""
        body = request.get_json(silent=True) or {}
        try:
            stored = svc.confirm_return(
                get_conn(), item_id, body.get("confirmed_by", "u900")
            )
        except svc.ServiceError as exc:
            return jsonify(error=str(exc)), exc.status
        return jsonify(stored), 200

    return app


if __name__ == "__main__":
    create_app().run(debug=True, port=5000)
