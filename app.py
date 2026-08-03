from datetime import datetime
import csv
from io import StringIO
import calendar as pycalendar
import re
import click

from flask import Flask, flash, jsonify, redirect, render_template, request, Response, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, inspect, or_, text
from werkzeug.security import check_password_hash, generate_password_hash

from services.ai_intake_service import AIIntakeExtractionService

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///intake.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = "change-this-in-production"

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message_category = "warning"

STAKEHOLDER_GROUP_OPTIONS = [
    "Marketing",
    "Operations",
    "Technology",
    "Sales",
    "Finance",
]

CONTENT_TYPE_OPTIONS = [
    "Documentation",
    "Web Content",
    "Training",
    "Report",
    "Other",
]

STATUS_OPTIONS = [
    "New",
    "In Progress",
    "On Hold",
    "Deferred",
    "Completed",
]

DEFAULT_STATUS = "On Hold"
PRIORITY_RULES = [
    {
        "min_score": 22,
        "label": "Critical",
        "slug": "critical",
        "badge_class": "bg-danger",
        "text_class": "text-danger",
        "accent_hex": "#dc2626",
        "chart_color": "#dc2626",
    },
    {
        "min_score": 17,
        "label": "High",
        "slug": "high",
        "badge_class": "bg-orange",
        "text_class": "priority-high-text",
        "accent_hex": "#fd7e14",
        "chart_color": "#fd7e14",
    },
    {
        "min_score": 11,
        "label": "Average",
        "slug": "average",
        "badge_class": "bg-warning text-dark",
        "text_class": "text-warning",
        "accent_hex": "#f59e0b",
        "chart_color": "#f59e0b",
    },
    {
        "min_score": 0,
        "label": "Low",
        "slug": "low",
        "badge_class": "bg-success",
        "text_class": "text-success",
        "accent_hex": "#16a34a",
        "chart_color": "#16a34a",
    },
]
PRIORITY_OPTIONS = [rule["label"] for rule in PRIORITY_RULES]
PRIORITY_LABEL_ALIASES = {
    "critical": "Critical",
    "high": "High",
    "average": "Average",
    "low": "Low",
}
USER_ROLE_OPTIONS = ["stakeholder", "internal", "admin"]
RATIONALE_FIELDS = [
    ("business_value_rationale", "Business Value"),
    ("reach_rationale", "Reach"),
    ("reuse_rationale", "Reuse"),
    ("risk_compliance_rationale", "Risk / Compliance"),
    ("feasibility_rationale", "Feasibility"),
]

ai_intake_service = AIIntakeExtractionService()


def _normalize_status(status):
    # Treat any legacy or unexpected status as the current supported fallback.
    return status if status in STATUS_OPTIONS else DEFAULT_STATUS


def _enforce_review_status_alignment(intake_request):
    # Reviewed requests are always tracked as In Progress to avoid conflicting workflow states.
    normalized_status = _normalize_status(intake_request.status)
    intake_request.status = "In Progress" if intake_request.reviewed else normalized_status


class Request(db.Model):
    # Core intake record used by both dashboard views and CSV export.
    id = db.Column(db.Integer, primary_key=True)
    request_number = db.Column(db.Integer, unique=True, nullable=False)
    project_name = db.Column(db.String(200), nullable=False)
    assigned_lead = db.Column(db.String(120), nullable=False)
    requested_by = db.Column(db.String(120), nullable=False)
    stakeholder_group = db.Column(db.String(120), nullable=False)
    content_type = db.Column(db.String(120), nullable=True)
    status = db.Column(db.String(60), nullable=False, default="New")
    description = db.Column(db.Text, nullable=True)
    business_outcome = db.Column(db.Text, nullable=False)
    reviewed = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.DateTime, nullable=True)
    visibility = db.Column(db.Integer, nullable=False, default=0)
    business_value = db.Column(db.Integer, nullable=False, default=0)
    reach = db.Column(db.Integer, nullable=False, default=0)
    reuse = db.Column(db.Integer, nullable=False, default=0)
    risk_compliance = db.Column(db.Integer, nullable=False, default=0)
    feasibility = db.Column(db.Integer, nullable=False, default=0)
    business_value_rationale = db.Column(db.Text, nullable=False, default="")
    reach_rationale = db.Column(db.Text, nullable=False, default="")
    reuse_rationale = db.Column(db.Text, nullable=False, default="")
    risk_compliance_rationale = db.Column(db.Text, nullable=False, default="")
    feasibility_rationale = db.Column(db.Text, nullable=False, default="")
    total_score = db.Column(db.Integer, nullable=False, default=0)
    priority = db.Column(db.String(30), nullable=False, default="Low")


class User(UserMixin, db.Model):
    # Authentication model used by Flask-Login.
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="stakeholder")

    def set_password(self, raw_password):
        self.password = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password, raw_password)

    def set_role(self, role):
        self.role = _normalize_user_role(role)


def _normalize_user_role(role):
    cleaned_role = (role or "").strip().lower()
    return cleaned_role if cleaned_role in USER_ROLE_OPTIONS else "stakeholder"


@app.cli.command("create-user")
@click.argument("username")
@click.argument("password")
@click.option("--role", default="stakeholder", help="stakeholder, internal, or admin")
def create_user(username, password, role):
    # Quick local bootstrap command for creating app users with hashed passwords.
    normalized_username = username.strip()
    if not normalized_username:
        raise click.BadParameter("username cannot be empty")

    normalized_role = _normalize_user_role(role)

    existing = User.query.filter(func.lower(User.username) == normalized_username.lower()).first()
    if existing:
        raise click.ClickException("A user with this username already exists.")

    user = User(username=normalized_username, role=normalized_role)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    click.echo(f"Created user '{normalized_username}' with role '{normalized_role}'.")


@login_manager.user_loader
def load_user(user_id):
    try:
        return User.query.get(int(user_id))
    except (TypeError, ValueError):
        return None


def _parse_score(value):
    # Enforce bounded integer scoring (0-5) even when form values are missing/invalid.
    try:
        return max(0, min(5, int(value)))
    except (TypeError, ValueError):
        return 0


def _priority_rule_for_score(total):
    # Resolve score to a single priority rule from the centralized threshold table.
    for rule in PRIORITY_RULES:
        if total >= rule["min_score"]:
            return rule
    return PRIORITY_RULES[-1]


def _priority_rule_for_label(priority_label):
    normalized = _normalize_priority_label(priority_label)
    for rule in PRIORITY_RULES:
        if rule["label"] == normalized:
            return rule
    return PRIORITY_RULES[-1]


def _calc_priority(total):
    # Keep score-to-label logic centralized in one place.
    return _priority_rule_for_score(total)["label"]


def _normalize_priority_label(priority_label):
    # Normalize legacy labels so old URLs/records keep working after terminology updates.
    normalized = (priority_label or "").strip().lower()
    return PRIORITY_LABEL_ALIASES.get(normalized, "")


def _parse_date(value):
    # Parse the HTML date input format (YYYY-MM-DD) into a Python date object.
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _parse_rationales(form_data):
    # Collect rationale inputs and identify any missing required entries.
    rationales = {}
    missing_labels = []
    for field_name, label in RATIONALE_FIELDS:
        value = form_data.get(field_name, "").strip()
        rationales[field_name] = value
        if not value:
            missing_labels.append(label)
    return rationales, missing_labels


def _can_edit_feasibility():
    return current_user.is_authenticated and current_user.role in {"internal", "admin"}


def _can_assign_project_lead():
    return current_user.is_authenticated and current_user.role in {"internal", "admin"}


def _can_select_requested_by():
    return current_user.is_authenticated and current_user.role in {"internal", "admin"}


def _normalize_title(title):
    # Normalize title for duplicate checks by ignoring punctuation/case/extra spaces.
    cleaned = re.sub(r"[^\w\s]", " ", (title or "").lower())
    return " ".join(cleaned.split())


def _titles_are_duplicate(title_a, title_b):
    # A duplicate is only an exact match after normalization.
    normalized_a = _normalize_title(title_a)
    normalized_b = _normalize_title(title_b)
    if not normalized_a or not normalized_b:
        return False
    return normalized_a == normalized_b


def _find_duplicate_title(project_name, exclude_request_id=None):
    # Return the first existing request whose title is a duplicate match, or None.
    if not project_name or not project_name.strip():
        return None
    query = Request.query
    if exclude_request_id is not None:
        query = query.filter(Request.id != exclude_request_id)
    for item in query.all():
        if _titles_are_duplicate(project_name, item.project_name):
            return item
    return None


def _resolve_dashboard_view(raw_view):
    requested_view = (raw_view or "").strip().lower()
    return "my" if requested_view == "my" else "all"


def _build_visibility_query(view_mode):
    query = Request.query
    if view_mode == "my" and current_user.role == "stakeholder":
        return query.filter(Request.requested_by == current_user.username)
    if view_mode == "my":
        return query.filter(
            or_(
                Request.assigned_lead == current_user.username,
                Request.requested_by == current_user.username,
            )
        )
    return query


def _can_edit_request(intake_request):
    if current_user.role in {"internal", "admin"}:
        return True
    if current_user.role == "stakeholder":
        return intake_request.requested_by == current_user.username and not intake_request.reviewed
    return False


def _can_delete_request(intake_request):
    if current_user.role == "admin":
        return True
    if current_user.role == "stakeholder":
        return intake_request.requested_by == current_user.username and not intake_request.reviewed
    return False


def _month_bounds(year, month):
    # Compute adjacent month values for calendar navigation.
    prev_month = month - 1
    prev_year = year
    if prev_month < 1:
        prev_month = 12
        prev_year -= 1

    next_month = month + 1
    next_year = year
    if next_month > 12:
        next_month = 1
        next_year += 1

    return (prev_year, prev_month), (next_year, next_month)


def _safe_csv_value(value):
    # Convert nullable/temporal values into predictable CSV strings.
    if value is None:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return str(value)


def _build_dashboard_query(args):
    # Reuse dashboard filtering rules for both UI listing and CSV export.
    search_filter = args.get("q", "").strip()
    stakeholder_group_filter = args.get("stakeholder_group", "").strip()
    requested_by_filter = args.get("requested_by", "").strip()
    assigned_lead_filter = args.get("assigned_lead", "").strip()
    status_filter = args.get("status", "").strip()
    priority_filter = _normalize_priority_label(args.get("priority", ""))
    start_date_from_raw = args.get("start_date_from", "").strip()
    start_date_to_raw = args.get("start_date_to", "").strip()
    start_date_from = _parse_date(start_date_from_raw) if start_date_from_raw else None
    start_date_to = _parse_date(start_date_to_raw) if start_date_to_raw else None
    sort_field = args.get("sort", "total_score").strip().lower()
    sort_order = args.get("order", "desc").strip().lower()
    view_mode = _resolve_dashboard_view(args.get("view", "all"))

    allowed_sort_fields = {
        "id": Request.request_number,
        "project_name": Request.project_name,
        "assigned_lead": Request.assigned_lead,
        "requested_by": Request.requested_by,
        "status": Request.status,
        "total_score": Request.total_score,
        "start_date": Request.start_date,
    }

    if sort_field not in allowed_sort_fields:
        sort_field = "total_score"
    if sort_order not in {"asc", "desc"}:
        sort_order = "desc"
    if status_filter and status_filter not in STATUS_OPTIONS:
        status_filter = ""
    if priority_filter and priority_filter not in PRIORITY_OPTIONS:
        priority_filter = ""

    query = _build_visibility_query(view_mode)

    if search_filter:
        search_clauses = [
            Request.assigned_lead.ilike(f"%{search_filter}%"),
            Request.project_name.ilike(f"%{search_filter}%"),
            Request.requested_by.ilike(f"%{search_filter}%"),
        ]
        if search_filter.isdigit():
            search_clauses.append(Request.request_number == int(search_filter))
        query = query.filter(or_(*search_clauses))
    if stakeholder_group_filter:
        query = query.filter(Request.stakeholder_group == stakeholder_group_filter)
    if requested_by_filter:
        query = query.filter(Request.requested_by == requested_by_filter)
    if assigned_lead_filter:
        query = query.filter(Request.assigned_lead == assigned_lead_filter)
    if status_filter:
        query = query.filter(Request.status == status_filter)
    if priority_filter:
        query = query.filter(Request.priority == priority_filter)

    warnings = []
    if start_date_from_raw and start_date_from is None:
        warnings.append("Start date (from) must be a valid date.")
    if start_date_to_raw and start_date_to is None:
        warnings.append("Start date (to) must be a valid date.")

    if start_date_from and start_date_to and start_date_from > start_date_to:
        warnings.append("Start date range is invalid: 'from' cannot be after 'to'.")
    else:
        if start_date_from:
            query = query.filter(Request.start_date >= start_date_from)
        if start_date_to:
            query = query.filter(Request.start_date <= start_date_to)

    sort_column = allowed_sort_fields[sort_field]
    if sort_order == "asc":
        query = query.order_by(sort_column.asc(), Request.created_at.desc())
    else:
        query = query.order_by(sort_column.desc(), Request.created_at.desc())

    filters = {
        "view": view_mode,
        "q": search_filter,
        "stakeholder_group": stakeholder_group_filter,
        "requested_by": requested_by_filter,
        "assigned_lead": assigned_lead_filter,
        "status": status_filter,
        "priority": priority_filter,
        "start_date_from": start_date_from_raw,
        "start_date_to": start_date_to_raw,
    }

    return query, filters, sort_field, sort_order, warnings


@app.route("/")
def index():
    return redirect(url_for("dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            flash("Username and password are required.", "error")
            return render_template("login.html")

        user = User.query.filter(func.lower(User.username) == username.lower()).first()
        if not user or not user.check_password(password):
            flash("Invalid username or password.", "error")
            return render_template("login.html")

        user.role = _normalize_user_role(user.role)

        login_user(user)
        next_url = request.args.get("next", "").strip()
        if next_url.startswith("/"):
            return redirect(next_url)
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = _normalize_user_role(request.form.get("role", "stakeholder"))

        if not username or not password:
            flash("Username and password are required.", "error")
            return render_template("register.html", role_options=USER_ROLE_OPTIONS, selected_role=role)

        duplicate = User.query.filter(func.lower(User.username) == username.lower()).first()
        if duplicate:
            flash("Username already exists.", "error")
            return render_template("register.html", role_options=USER_ROLE_OPTIONS, selected_role=role)

        user = User(username=username, role=role)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        flash("Registration successful. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html", role_options=USER_ROLE_OPTIONS, selected_role="stakeholder")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Signed out successfully.", "success")
    return redirect(url_for("login"))


@app.route("/users", methods=["GET", "POST"])
@login_required
def users():
    if current_user.role != "admin":
        flash("Only admins can access user management.", "error")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = _normalize_user_role(request.form.get("role", "stakeholder"))

        if not username or not password:
            flash("Username and password are required.", "error")
            return redirect(url_for("users"))

        duplicate = User.query.filter(func.lower(User.username) == username.lower()).first()
        if duplicate:
            flash("Username already exists.", "error")
            return redirect(url_for("users"))

        new_user = User(username=username, role=role)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()
        flash("User created successfully.", "success")
        return redirect(url_for("users"))

    all_users = User.query.order_by(User.username.asc()).all()
    return render_template("users.html", users=all_users, role_options=USER_ROLE_OPTIONS)


@app.route("/edit_user/<int:user_id>", methods=["GET", "POST"])
@login_required
def edit_user(user_id):
    if current_user.role != "admin":
        flash("Only admins can modify users.", "error")
        return redirect(url_for("dashboard"))

    user = User.query.get_or_404(user_id)

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        role = _normalize_user_role(request.form.get("role", user.role))

        if not username:
            flash("Username is required.", "error")
            return render_template("edit_user.html", edit_target=user, role_options=USER_ROLE_OPTIONS)

        duplicate = User.query.filter(
            func.lower(User.username) == username.lower(),
            User.id != user.id,
        ).first()
        if duplicate:
            flash("Username already exists.", "error")
            return render_template("edit_user.html", edit_target=user, role_options=USER_ROLE_OPTIONS)

        user.username = username
        user.role = role
        db.session.commit()
        flash("User updated successfully.", "success")
        return redirect(url_for("users"))

    return render_template("edit_user.html", edit_target=user, role_options=USER_ROLE_OPTIONS)


@app.route("/change_password/<int:user_id>", methods=["GET", "POST"])
@login_required
def change_password(user_id):
    if current_user.role != "admin":
        flash("Only admins can change user passwords.", "error")
        return redirect(url_for("dashboard"))

    user = User.query.get_or_404(user_id)

    if request.method == "POST":
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        has_error = False
        if not new_password:
            flash("New password cannot be empty.", "error")
            has_error = True
        elif len(new_password) < 8:
            flash("New password must be at least 8 characters.", "error")
            has_error = True

        if new_password != confirm_password:
            flash("Passwords must match.", "error")
            has_error = True

        if has_error:
            return render_template("change_password.html", user_target=user)

        user.password = generate_password_hash(new_password)
        db.session.commit()
        flash("Password updated successfully.", "success")
        return redirect(url_for("users"))

    return render_template("change_password.html", user_target=user)


@app.route("/delete_user/<int:user_id>", methods=["POST"])
@login_required
def delete_user(user_id):
    if current_user.role != "admin":
        flash("Only admins can delete users.", "error")
        return redirect(url_for("dashboard"))

    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash("You cannot delete your own account while logged in.", "error")
        return redirect(url_for("users"))

    db.session.delete(user)
    db.session.commit()
    flash("User deleted successfully.", "success")
    return redirect(url_for("users"))


@app.context_processor
def inject_download_url():
    # Keep CSV export aligned with dashboard filters/sort in the current URL.
    dashboard_params = {
        key: value
        for key, value in request.args.items()
        if key in {
            "view",
            "q",
            "stakeholder_group",
            "requested_by",
            "assigned_lead",
            "status",
            "priority",
            "start_date_from",
            "start_date_to",
            "sort",
            "order",
        }
        and value
    }
    if request.endpoint == "dashboard" and dashboard_params:
        download_url = url_for("download_csv", **dashboard_params)
    else:
        download_url = url_for("download_csv")

    return {
        "download_url": download_url,
        "priority_levels": PRIORITY_RULES,
        "priority_meta_for_score": _priority_rule_for_score,
        "priority_meta_for_label": _priority_rule_for_label,
    }


@app.route("/dashboard")
@login_required
def dashboard():
    query, filters, sort_field, sort_order, warnings = _build_dashboard_query(request.args)
    for message in warnings:
        flash(message, "warning")

    requests = query.all()
    all_requests = _build_visibility_query(filters["view"]).all()
    visible_statuses = [_normalize_status(item.status) for item in all_requests]

    # Dashboard summary reflects the whole portfolio, not just the currently filtered rows.
    summary = {
        "total_projects": len({item.project_name for item in all_requests if item.project_name}),
        "active_requests": sum(1 for status in visible_statuses if status != "Completed"),
        "new_requests": sum(1 for status in visible_statuses if status == "New"),
        "on_hold": sum(1 for status in visible_statuses if status == "On Hold"),
        "completed": sum(1 for status in visible_statuses if status == "Completed"),
        "average_capability_score": round(
            sum(item.total_score for item in all_requests) / len(all_requests),
            2,
        ) if all_requests else 0.0,
        "total_requests": len(all_requests),
        "active_count": sum(1 for status in visible_statuses if status not in {"Completed"}),
        "new_count": sum(1 for status in visible_statuses if status == "New"),
        "completed_count": sum(1 for status in visible_statuses if status == "Completed"),
    }

    stakeholder_values = sorted({item.stakeholder_group for item in all_requests if item.stakeholder_group})
    requested_by_values = sorted({item.requested_by for item in all_requests if item.requested_by})
    assigned_lead_values = sorted({item.assigned_lead for item in all_requests if item.assigned_lead})
    return render_template(
        "dashboard.html",
        requests=requests,
        summary=summary,
        filters=filters,
        current_sort=sort_field,
        current_order=sort_order,
        current_view=filters["view"],
        can_view_all_requests=True,
        filter_options={
            "stakeholder_groups": sorted(set(STAKEHOLDER_GROUP_OPTIONS + stakeholder_values)),
            "requested_by": requested_by_values,
            "assigned_lead": assigned_lead_values,
            "statuses": STATUS_OPTIONS,
        },
        normalize_status=_normalize_status,
    )


@app.route("/calendar")
@login_required
def calendar_view():
    today = datetime.utcnow().date()

    try:
        year = int(request.args.get("year", today.year))
    except (TypeError, ValueError):
        year = today.year
    if year < 1 or year > 9999:
        year = today.year

    try:
        month = int(request.args.get("month", today.month))
    except (TypeError, ValueError):
        month = today.month

    if month < 1 or month > 12:
        month = today.month

    month_start = datetime(year, month, 1).date()
    if month == 12:
        next_month_start = datetime(year + 1, 1, 1).date()
    else:
        next_month_start = datetime(year, month + 1, 1).date()

    month_requests = (
        Request.query.filter(Request.start_date.isnot(None))
        .filter(Request.start_date >= month_start)
        .filter(Request.start_date < next_month_start)
        .order_by(Request.start_date.asc(), Request.total_score.desc(), Request.project_name.asc())
        .all()
    )

    requests_by_day = {}
    for item in month_requests:
        requests_by_day.setdefault(item.start_date, []).append(item)

    for day_requests in requests_by_day.values():
        day_requests.sort(key=lambda item: (-item.total_score, item.priority, item.project_name.lower()))

    month_weeks = []
    for week in pycalendar.Calendar(firstweekday=0).monthdatescalendar(year, month):
        week_days = []
        for day in week:
            if day.weekday() >= 5:
                continue
            week_days.append(
                {
                    "date": day,
                    "day": day.day,
                    "is_current_month": day.month == month,
                    "is_today": day == today,
                    "requests": requests_by_day.get(day, []),
                }
            )
        month_weeks.append(week_days)

    (prev_year, prev_month), (next_year, next_month) = _month_bounds(year, month)

    return render_template(
        "calendar.html",
        current_year=year,
        current_month=month,
        month_name=pycalendar.month_name[month],
        month_weeks=month_weeks,
        day_labels=list(pycalendar.day_abbr[:5]),
        prev_month_url=url_for("calendar_view", year=prev_year, month=prev_month),
        next_month_url=url_for("calendar_view", year=next_year, month=next_month),
        today=today,
    )


@app.route("/insights")
@login_required
def insights():
    all_requests = Request.query.all()

    priority_order = [rule["label"] for rule in PRIORITY_RULES]
    priority_counts = {
        label: sum(1 for item in all_requests if _normalize_priority_label(item.priority) == label)
        for label in priority_order
    }

    status_order = STATUS_OPTIONS
    status_counts = {
        label: sum(1 for item in all_requests if _normalize_status(item.status) == label)
        for label in status_order
    }

    status_color_map = {
        "New": "#2563eb",
        "In Progress": "#d97706",
        "On Hold": "#6b7280",
        "Deferred": "#0ea5a4",
        "Completed": "#16a34a",
    }

    timeline_counts = {}
    for item in all_requests:
        if not item.start_date:
            continue
        key = item.start_date.strftime("%Y-%m-%d")
        timeline_counts[key] = timeline_counts.get(key, 0) + 1

    timeline_labels = sorted(timeline_counts.keys())
    timeline_values = [timeline_counts[label] for label in timeline_labels]

    return render_template(
        "insights.html",
        total_requests=len(all_requests),
        priority_labels=priority_order,
        priority_values=[priority_counts[label] for label in priority_order],
        priority_colors=[rule["chart_color"] for rule in PRIORITY_RULES],
        status_labels=status_order,
        status_values=[status_counts[label] for label in status_order],
        status_colors=[status_color_map[label] for label in status_order],
        timeline_labels=timeline_labels,
        timeline_values=timeline_values,
    )


@app.route("/settings")
@login_required
def settings():
    return render_template("settings.html")


@app.route("/guide")
@login_required
def guide():
    return render_template("guide.html")


@app.route("/api/ai/intake-extract", methods=["POST"])
@login_required
def ai_extract_intake_request():
    payload = request.get_json(silent=True) or {}
    source_text = (payload.get("text") or "").strip()

    if not source_text:
        return jsonify(
            {
                "ok": False,
                "message": "Please provide audio transcript or request text.",
                "data": {},
                "extracted_fields": [],
                "missing_fields": [],
            }
        ), 400

    try:
        extraction_result = ai_intake_service.extract(source_text)
    except RuntimeError as exc:
        return jsonify(
            {
                "ok": False,
                "message": str(exc),
                "data": {},
                "extracted_fields": [],
                "missing_fields": [],
            }
        ), 503
    except Exception:
        app.logger.exception("AI intake extraction failed.")
        return jsonify(
            {
                "ok": False,
                "message": "AI extraction failed. Please try again.",
                "data": {},
                "extracted_fields": [],
                "missing_fields": [],
            }
        ), 500

    return jsonify({"ok": True, **extraction_result})


@app.route("/new", methods=["GET", "POST"])
@login_required
def new_request():
    def _next_request_number():
        max_request_number = db.session.query(func.max(Request.request_number)).scalar() or 0
        return max_request_number + 1

    def _render_new_form(form_data=None):
        if hasattr(form_data, "to_dict"):
            form_data = form_data.to_dict()
        users = User.query.order_by(User.username.asc()).all()
        return render_template(
            "request_form.html",
            form_data=form_data,
            intake_request=None,
            page_title="New Request",
            auto_request_number=_next_request_number(),
            stakeholder_group_options=STAKEHOLDER_GROUP_OPTIONS,
            status_options=STATUS_OPTIONS,
            normalize_status=_normalize_status,
            can_edit_feasibility=_can_edit_feasibility(),
            can_assign_lead=_can_assign_project_lead(),
            can_select_requested_by=_can_select_requested_by(),
            users=users,
            default_requested_by=current_user.username,
            ai_intake_configured=ai_intake_service.is_configured(),
        )

    if request.method == "POST":
        project_name = request.form.get("project_name", "").strip()
        assigned_lead = request.form.get("assigned_lead", "").strip()
        requested_by = request.form.get("requested_by", current_user.username).strip() or current_user.username
        business_outcome = request.form.get("business_outcome", "").strip()
        valid_usernames = {user.username for user in User.query.with_entities(User.username).all()}

        if not _can_select_requested_by():
            requested_by = current_user.username
        elif not requested_by:
            flash("Requested By is required.", "error")
            return _render_new_form(form_data=request.form)
        elif requested_by not in valid_usernames:
            flash("Requested By must be a valid user.", "error")
            return _render_new_form(form_data=request.form)

        if not _can_assign_project_lead():
            assigned_lead = "Assigned by internal team"
        stakeholder_group = request.form.get("stakeholder_group", "").strip()
        start_date_raw = request.form.get("start_date", "").strip()
        start_date = _parse_date(start_date_raw)
        status = request.form.get("status", "New").strip()
        description = request.form.get("description", "").strip()
        if status not in STATUS_OPTIONS:
            status = DEFAULT_STATUS

        # Priority is derived from five weighted dimensions captured in the form.
        business_value = _parse_score(request.form.get("business_value", 0))
        reach = _parse_score(request.form.get("reach", 0))
        reuse = _parse_score(request.form.get("reuse", 0))
        risk_compliance = _parse_score(request.form.get("risk_compliance", 0))
        feasibility = _parse_score(request.form.get("feasibility", 0)) if _can_edit_feasibility() else 0
        rationales, missing_rationale_labels = _parse_rationales(request.form)
        if not _can_edit_feasibility():
            rationales["feasibility_rationale"] = "Auto-set for stakeholder role."
            missing_rationale_labels = [
                label
                for field_name, label in RATIONALE_FIELDS
                if field_name != "feasibility_rationale" and not rationales[field_name]
            ]
        total_score = business_value + reach + reuse + risk_compliance + feasibility
        priority = _calc_priority(total_score)

        if missing_rationale_labels:
            flash(
                "Rationale is required for: " + ", ".join(missing_rationale_labels) + ".",
                "error",
            )
            return _render_new_form(form_data=request.form)

        if not business_outcome:
            flash("Business Outcome is required.", "error")
            return _render_new_form(form_data=request.form)

        if not start_date_raw or start_date is None:
            flash("Start date is required and must be a valid date.", "error")
            return _render_new_form(form_data=request.form)

        duplicate_match = _find_duplicate_title(project_name)
        if duplicate_match:
            flash(
                f"A similar project already exists. Please review existing requests before submitting."
                f" Matching project: \"{duplicate_match.project_name}\"",
                "error",
            )
            return _render_new_form(form_data=request.form)

        request_number = _next_request_number()

        new_item = Request(
            request_number=request_number,
            project_name=project_name,
            assigned_lead=assigned_lead,
            requested_by=requested_by,
            stakeholder_group=stakeholder_group,
            start_date=start_date,
            status=status,
            description=description,
            business_outcome=business_outcome,
            # If an intake is created as completed, stamp completion immediately.
            end_date=datetime.utcnow() if status == "Completed" else None,
            visibility=0,
            business_value=business_value,
            reach=reach,
            reuse=reuse,
            risk_compliance=risk_compliance,
            feasibility=feasibility,
            business_value_rationale=rationales["business_value_rationale"],
            reach_rationale=rationales["reach_rationale"],
            reuse_rationale=rationales["reuse_rationale"],
            risk_compliance_rationale=rationales["risk_compliance_rationale"],
            feasibility_rationale=rationales["feasibility_rationale"],
            total_score=total_score,
            priority=priority,
        )
        db.session.add(new_item)
        db.session.commit()
        flash("Request created successfully.", "success")
        return redirect(url_for("dashboard"))

    return _render_new_form(form_data=None)


@app.route("/edit/<int:request_id>", methods=["GET", "POST"])
@login_required
def edit_request(request_id):
    intake_request = Request.query.get_or_404(request_id)

    def _render_edit_form(form_data=None):
        if hasattr(form_data, "to_dict"):
            form_data = form_data.to_dict()
        return render_template(
            "request_form.html",
            form_data=form_data,
            page_title="Edit Request",
            intake_request=intake_request,
            stakeholder_group_options=STAKEHOLDER_GROUP_OPTIONS,
            status_options=STATUS_OPTIONS,
            normalize_status=_normalize_status,
            can_edit_feasibility=_can_edit_feasibility(),
            can_assign_lead=_can_assign_project_lead(),
            can_select_requested_by=_can_select_requested_by(),
            users=User.query.order_by(User.username.asc()).all(),
            default_requested_by=current_user.username,
            ai_intake_configured=ai_intake_service.is_configured(),
        )

    if not _can_edit_request(intake_request):
        if current_user.role == "stakeholder" and intake_request.requested_by == current_user.username and intake_request.reviewed:
            flash("This request has been reviewed and can no longer be edited.", "error")
        else:
            flash("Access denied. You can only edit your own requests.", "error")
        return redirect(url_for("view_request", request_id=intake_request.id))

    if request.method == "POST":
        request_number_raw = request.form.get("request_number", "").strip()
        project_name = request.form.get("project_name", "").strip()
        assigned_lead = request.form.get("assigned_lead", "").strip() if _can_assign_project_lead() else intake_request.assigned_lead
        requested_by = request.form.get("requested_by", "").strip()
        business_outcome = request.form.get("business_outcome", "").strip()
        valid_usernames = {user.username for user in User.query.with_entities(User.username).all()}
        if not _can_select_requested_by():
            requested_by = current_user.username
        elif not requested_by:
            flash("Requested By is required.", "error")
            return _render_edit_form(form_data=request.form)
        elif requested_by not in valid_usernames:
            flash("Requested By must be a valid user.", "error")
            return _render_edit_form(form_data=request.form)
        stakeholder_group = request.form.get("stakeholder_group", "").strip()
        start_date_raw = request.form.get("start_date", "").strip()
        start_date = _parse_date(start_date_raw)
        status = request.form.get("status", "").strip()
        description = request.form.get("description", "").strip()
        if status not in STATUS_OPTIONS:
            status = DEFAULT_STATUS
        if intake_request.reviewed and status != "In Progress":
            status = "In Progress"
            flash("Reviewed requests stay in In Progress status.", "info")

        # Recalculate score/priority on every edit to keep stored values consistent.
        business_value = _parse_score(request.form.get("business_value", 0))
        reach = _parse_score(request.form.get("reach", 0))
        reuse = _parse_score(request.form.get("reuse", 0))
        risk_compliance = _parse_score(request.form.get("risk_compliance", 0))
        feasibility = _parse_score(request.form.get("feasibility", 0)) if _can_edit_feasibility() else intake_request.feasibility
        rationales, missing_rationale_labels = _parse_rationales(request.form)
        if not _can_edit_feasibility():
            rationales["feasibility_rationale"] = intake_request.feasibility_rationale
            missing_rationale_labels = [
                label
                for field_name, label in RATIONALE_FIELDS
                if field_name != "feasibility_rationale" and not rationales[field_name]
            ]
        total_score = business_value + reach + reuse + risk_compliance + feasibility
        priority = _calc_priority(total_score)

        if missing_rationale_labels:
            flash(
                "Rationale is required for: " + ", ".join(missing_rationale_labels) + ".",
                "error",
            )
            return _render_edit_form(form_data=request.form)

        if not business_outcome:
            flash("Business Outcome is required.", "error")
            return _render_edit_form(form_data=request.form)

        if not start_date_raw or start_date is None:
            flash("Start date is required and must be a valid date.", "error")
            return _render_edit_form(form_data=request.form)

        if not request_number_raw:
            flash("Request number is required.", "error")
            return _render_edit_form(form_data=request.form)

        if not request_number_raw.isdigit():
            flash("Request number must be an integer.", "error")
            return _render_edit_form(form_data=request.form)

        request_number = int(request_number_raw)

        duplicate = Request.query.filter(
            Request.request_number == request_number,
            Request.id != intake_request.id,
        ).first()

        if duplicate:
            flash("Request number must be unique.", "error")
            return _render_edit_form(form_data=request.form)

        duplicate_match = _find_duplicate_title(project_name, exclude_request_id=intake_request.id)
        if duplicate_match:
            flash(
                f"A similar project already exists. Please review existing requests before submitting."
                f" Matching project: \"{duplicate_match.project_name}\"",
                "error",
            )
            return _render_edit_form(form_data=request.form)

        # Auto-set end_date when status first becomes Completed; clear it otherwise
        if status == "Completed" and intake_request.end_date is None:
            intake_request.end_date = datetime.utcnow()
        elif status != "Completed":
            intake_request.end_date = None

        intake_request.request_number = request_number
        intake_request.project_name = project_name
        intake_request.assigned_lead = assigned_lead
        intake_request.requested_by = requested_by
        intake_request.stakeholder_group = stakeholder_group
        intake_request.start_date = start_date
        intake_request.status = status
        intake_request.description = description
        intake_request.business_outcome = business_outcome
        intake_request.visibility = intake_request.visibility if intake_request.visibility is not None else 0
        intake_request.business_value = business_value
        intake_request.reach = reach
        intake_request.reuse = reuse
        intake_request.risk_compliance = risk_compliance
        intake_request.feasibility = feasibility
        intake_request.business_value_rationale = rationales["business_value_rationale"]
        intake_request.reach_rationale = rationales["reach_rationale"]
        intake_request.reuse_rationale = rationales["reuse_rationale"]
        intake_request.risk_compliance_rationale = rationales["risk_compliance_rationale"]
        intake_request.feasibility_rationale = rationales["feasibility_rationale"]
        intake_request.total_score = total_score
        intake_request.priority = priority
        _enforce_review_status_alignment(intake_request)

        db.session.commit()
        flash("Request updated successfully.", "success")
        return redirect(url_for("dashboard"))

    return _render_edit_form(form_data=None)


@app.route("/view/<int:request_id>", methods=["GET"])
@login_required
def view_request(request_id):
    intake_request = Request.query.get_or_404(request_id)
    return render_template(
        "request_detail.html",
        intake_request=intake_request,
        normalize_status=_normalize_status,
    )


@app.route("/review/<int:id>", methods=["POST"])
@login_required
def mark_request_reviewed(id):
    if current_user.role not in {"internal", "admin"}:
        flash("Only internal and admin users can mark requests as reviewed.", "error")
        return redirect(url_for("view_request", request_id=id))

    intake_request = Request.query.get_or_404(id)
    if intake_request.reviewed:
        flash("Request is already marked as reviewed.", "info")
        return redirect(url_for("view_request", request_id=id))

    intake_request.reviewed = True
    intake_request.status = "In Progress"
    intake_request.end_date = None
    db.session.commit()
    flash("Request marked as reviewed.", "success")
    return redirect(url_for("view_request", request_id=id))


@app.route("/unreview/<int:id>", methods=["POST"])
@login_required
def unmark_request_reviewed(id):
    if current_user.role not in {"internal", "admin"}:
        flash("Only internal and admin users can unmark reviewed requests.", "error")
        return redirect(url_for("view_request", request_id=id))

    intake_request = Request.query.get_or_404(id)
    if not intake_request.reviewed:
        flash("Request is already pending review.", "info")
        return redirect(url_for("view_request", request_id=id))

    intake_request.reviewed = False
    if intake_request.status == "In Progress":
        intake_request.status = "New"
    else:
        intake_request.status = _normalize_status(intake_request.status)
    db.session.commit()
    flash("Request marked as pending review.", "success")
    return redirect(url_for("view_request", request_id=id))


@app.route("/delete/<int:id>", methods=["POST"])
@login_required
def delete_request(id):
    intake_request = Request.query.get_or_404(id)

    if not _can_delete_request(intake_request):
        if current_user.role == "stakeholder" and intake_request.requested_by != current_user.username:
            flash("Access denied. You can only delete your own requests.", "error")
        elif current_user.role == "stakeholder" and intake_request.reviewed:
            flash("Reviewed requests cannot be deleted by stakeholders.", "error")
        else:
            flash("Only request owners and admins can delete requests.", "error")
        return redirect(url_for("view_request", request_id=intake_request.id))

    db.session.delete(intake_request)
    db.session.commit()
    flash("Request deleted successfully.", "success")
    return redirect(url_for("dashboard"))


@app.route("/download")
@login_required
def download_csv():
    query, _, _, _, _ = _build_dashboard_query(request.args)
    filtered_requests = query.all()
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Request Number",
            "Project Name",
            "Assigned Lead",
            "Requested By",
            "Stakeholder Group",
            "Content Type",
            "Status",
            "Priority",
            "Start Date",
            "End Date",
            "Total Score",
            "Business Value",
            "Reach",
            "Reuse",
            "Risk / Compliance",
            "Feasibility",
            "Description",
            "Created Date",
        ]
    )

    for item in filtered_requests:
        writer.writerow(
            [
                _safe_csv_value(item.request_number),
                _safe_csv_value(item.project_name),
                _safe_csv_value(item.assigned_lead),
                _safe_csv_value(item.requested_by),
                _safe_csv_value(item.stakeholder_group),
                _safe_csv_value(item.content_type),
                _safe_csv_value(_normalize_status(item.status)),
                _safe_csv_value(item.priority),
                _safe_csv_value(item.start_date),
                _safe_csv_value(item.end_date),
                _safe_csv_value(item.total_score),
                _safe_csv_value(item.business_value),
                _safe_csv_value(item.reach),
                _safe_csv_value(item.reuse),
                _safe_csv_value(item.risk_compliance),
                _safe_csv_value(item.feasibility),
                _safe_csv_value(item.description),
                _safe_csv_value(item.created_at),
            ]
        )

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=intake_requests.csv"},
    )


with app.app_context():
    # Ensure the SQLite schema exists before serving requests.
    db.create_all()
    inspector = inspect(db.engine)
    existing_columns = {column["name"] for column in inspector.get_columns("request")}

    if "start_date" not in existing_columns:
        db.session.execute(text("ALTER TABLE request ADD COLUMN start_date DATE"))
        db.session.commit()

    if "visibility" not in existing_columns:
        db.session.execute(text("ALTER TABLE request ADD COLUMN visibility INTEGER DEFAULT 0"))
        db.session.execute(text("UPDATE request SET visibility = 0 WHERE visibility IS NULL"))
        db.session.commit()
    else:
        db.session.execute(text("UPDATE request SET visibility = 0 WHERE visibility IS NULL"))
        db.session.commit()

    for field_name, _ in RATIONALE_FIELDS:
        if field_name not in existing_columns:
            db.session.execute(text(f"ALTER TABLE request ADD COLUMN {field_name} TEXT DEFAULT ''"))
            db.session.execute(text(f"UPDATE request SET {field_name} = '' WHERE {field_name} IS NULL"))
            db.session.commit()

    if "business_outcome" not in existing_columns:
        db.session.execute(text("ALTER TABLE request ADD COLUMN business_outcome TEXT DEFAULT ''"))
        db.session.execute(text("UPDATE request SET business_outcome = '' WHERE business_outcome IS NULL"))
        db.session.commit()

    if "reviewed" not in existing_columns:
        db.session.execute(text("ALTER TABLE request ADD COLUMN reviewed BOOLEAN DEFAULT 0"))
        db.session.execute(text("UPDATE request SET reviewed = 0 WHERE reviewed IS NULL"))
        db.session.commit()

    # Keep persisted totals/priorities in sync when scoring rules change.
    requests = Request.query.all()
    scoring_changed = False
    for item in requests:
        recalculated_total = item.business_value + item.reach + item.reuse + item.risk_compliance + item.feasibility
        recalculated_priority = _calc_priority(recalculated_total)
        if item.total_score != recalculated_total or item.priority != recalculated_priority:
            item.total_score = recalculated_total
            item.priority = recalculated_priority
            scoring_changed = True
    if scoring_changed:
        db.session.commit()

    status_normalized = False
    for item in requests:
        normalized_status = _normalize_status(item.status)
        if item.status != normalized_status:
            item.status = normalized_status
            status_normalized = True
        if item.reviewed and item.status != "In Progress":
            item.status = "In Progress"
            status_normalized = True
    if status_normalized:
        db.session.commit()

    if "user" in inspector.get_table_names():
        user_columns = {column["name"] for column in inspector.get_columns("user")}
        if "password" not in user_columns:
            db.session.execute(text("ALTER TABLE user ADD COLUMN password TEXT DEFAULT ''"))
            if "password_hash" in user_columns:
                db.session.execute(
                    text(
                        "UPDATE user "
                        "SET password = password_hash "
                        "WHERE (password IS NULL OR TRIM(password) = '') "
                        "AND password_hash IS NOT NULL"
                    )
                )
            db.session.commit()
            user_columns.add("password")

        if "role" not in user_columns:
            db.session.execute(text("ALTER TABLE user ADD COLUMN role TEXT DEFAULT 'stakeholder'"))
            db.session.execute(text("UPDATE user SET role = 'stakeholder' WHERE role IS NULL OR TRIM(role) = ''"))
            db.session.commit()
        else:
            db.session.execute(
                text(
                    "UPDATE user "
                    "SET role = 'stakeholder' "
                    "WHERE role IS NULL OR TRIM(role) = '' "
                    "OR LOWER(TRIM(role)) NOT IN ('stakeholder', 'internal', 'admin')"
                )
            )
            db.session.commit()

        db.session.execute(
            text(
                "UPDATE user "
                "SET password = '' "
                "WHERE password IS NULL"
            )
        )
        db.session.commit()


if __name__ == "__main__":
    app.run(debug=True)
