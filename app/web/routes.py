"""HTML pages. They call the same services as the JSON API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import ROOT
from app.constants import (
    DATA_FIELDS,
    LABEL_BY_FIELD,
    PARTIES,
    SEARCH_FILTER_FIELDS,
    USER_EDITABLE_FIELDS,
)
from app.database import get_db
from app.deps import resolve_user, source_for
from app.errors import ServiceError
from app.schemas import PasswordChange, ResetPassword, UserCreate, VoterSearch, VoterWrite
from app.security import create_access_token
from app.services.impex import ImportRejected, import_csv, voters_to_csv, voters_to_xlsx
from app.services.login import (
    INVALID_LOGIN_MESSAGE,
    LoginThrottled,
    attempt_login,
    guarded_change_password,
)
from app.services.master_data import ENTITIES, build_master_table, tab_list
from app.services.reports import build_report
from app.services.users import create_user, delete_user, list_users, reset_user_password
from app.services.voters import (
    create_voter,
    export_voters,
    get_active,
    is_privileged,
    replace_voter,
    search_voters,
    soft_delete,
)
from app.validation import local_label
from app.services.voters import VOTER_ID_LOCKED_MESSAGE
from app.web.templates import render

router = APIRouter()

DEFAULT_MASTER_TAB = "constituency"


class LoginRequired(Exception):
    def __init__(self, next_path: str) -> None:
        self.next_path = next_path


def safe_next(value: str | None) -> str:
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/voters"
    return value


class PasswordChangeRequired(Exception):
    """Raised for a signed-in web user whose password must be changed first."""


def web_session_user(request: Request, db: Session = Depends(get_db)):
    """Signed-in user, even when a password change is pending."""
    user = resolve_user(request, db, None)
    if user is None:
        raise LoginRequired(request.url.path)
    return user


def web_user(user=Depends(web_session_user)):
    if user.must_change_password:
        raise PasswordChangeRequired()
    return user


def _form_errors(exc: ValidationError) -> list[str]:
    lines = []
    for err in exc.errors():
        loc = err.get("loc") or ()
        field = str(loc[-1]) if loc else "form"
        label = LABEL_BY_FIELD.get(field, field.replace("_", " ").capitalize())
        message = err["msg"]
        if err.get("type") == "string_too_short":
            message = f"must be at least {(err.get('ctx') or {}).get('min_length', 1)} characters"
        prefix = "Value error, "
        if message.startswith(prefix):
            message = message[len(prefix) :]
        lines.append(f"{label}: {message}")
    return lines


def _voter_form(form) -> dict:
    return {field: str(form.get(field) or "") for field in DATA_FIELDS}


def _stored_values(voter) -> dict:
    """The saved row as form strings. Local shows as Local / Non-Local."""
    values = {
        field: getattr(voter, field) if getattr(voter, field) is not None else ""
        for field in DATA_FIELDS
    }
    values["is_local"] = local_label(voter.is_local)
    return values


def _set_cookie(response: Response, request: Request, user) -> None:
    settings = request.app.state.settings
    token = create_access_token(user, settings)
    response.set_cookie(
        settings.cookie_name,
        token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.jwt_expire_minutes * 60,
        path="/",
    )


@router.get("/")
def home(request: Request, db: Session = Depends(get_db)):
    user = resolve_user(request, db, None)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    return RedirectResponse(_landing(user), status_code=303)


def _landing(user, nxt: str = "/voters") -> str:
    """Where a signed-in user may go: the password gate comes before any other page."""
    return "/change-password" if user.must_change_password else nxt


@router.get("/login")
def login_form(request: Request, db: Session = Depends(get_db)):
    user = resolve_user(request, db, None)
    if user is not None:
        return RedirectResponse(
            _landing(user, safe_next(request.query_params.get("next"))), status_code=303
        )
    return render(
        request,
        "login.html",
        {"next": safe_next(request.query_params.get("next")), "error": None},
    )


@router.post("/login")
async def login_submit(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    username = str(form.get("username") or "")
    password = str(form.get("password") or "")
    nxt = safe_next(str(form.get("next") or ""))
    try:
        user = attempt_login(db, request, username, password)
    except LoginThrottled as exc:
        blocked = render(
            request,
            "login.html",
            {"next": nxt, "error": "Too many failed attempts. Try again later."},
            status_code=429,
        )
        blocked.headers["Retry-After"] = str(exc.retry_after)
        return blocked
    if user is None:
        return render(
            request,
            "login.html",
            {"next": nxt, "error": INVALID_LOGIN_MESSAGE},
            status_code=401,
        )
    response = RedirectResponse(_landing(user, nxt), status_code=303)
    _set_cookie(response, request, user)
    return response


def _change_password_page(request: Request, user, errors: list[str], status_code: int = 200):
    return render(
        request,
        "change_password.html",
        {"user": user, "errors": errors, "forced": user.must_change_password},
        status_code=status_code,
    )


@router.get("/change-password")
def change_password_form(request: Request, user=Depends(web_session_user)):
    return _change_password_page(request, user, [])


@router.post("/change-password")
async def change_password_submit(
    request: Request, db: Session = Depends(get_db), user=Depends(web_session_user)
):
    form = await request.form()
    try:
        body = PasswordChange.model_validate(
            {
                "current_password": str(form.get("current_password") or ""),
                "new_password": str(form.get("new_password") or ""),
            }
        )
    except ValidationError as exc:
        return _change_password_page(request, user, _form_errors(exc), 400)
    try:
        updated = guarded_change_password(
            db, request, user, body.current_password, body.new_password
        )
    except LoginThrottled as exc:
        blocked = _change_password_page(
            request, user, ["Too many failed attempts. Try again later."], 429
        )
        blocked.headers["Retry-After"] = str(exc.retry_after)
        return blocked
    except ServiceError as exc:
        return _change_password_page(request, user, [exc.message], exc.status_code)
    # Every earlier token was revoked by the change; keep this browser signed in.
    response = RedirectResponse("/voters", status_code=303)
    _set_cookie(response, request, updated)
    return response


@router.post("/logout")
def logout(request: Request):
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(request.app.state.settings.cookie_name, path="/")
    return response


def _forbidden(request: Request, user):
    return render(request, "403.html", {"user": user}, status_code=403)


def _search_from_form(form) -> VoterSearch:
    """Read filters from a posted form. Blank fields are dropped."""
    keys = (*SEARCH_FILTER_FIELDS, "page", "page_size")
    values = {key: str(form.get(key)) for key in keys if form.get(key) not in (None, "")}
    return VoterSearch.model_validate(values)


def _filter_values(query: VoterSearch) -> dict[str, str]:
    """Active filters, for hidden inputs on the pager and export forms."""
    return {
        key: str(getattr(query, key))
        for key in SEARCH_FILTER_FIELDS
        if getattr(query, key) not in (None, "")
    }


def _render_list(request: Request, db: Session, user, query: VoterSearch):
    rows, total = search_voters(db, query)
    return render(
        request,
        "voters/list.html",
        {
            "user": user,
            "voters": rows,
            "total": total,
            "query": query,
            "error": None,
            "filter_values": _filter_values(query),
            "parties": PARTIES,
        },
    )


def _list_error(request: Request, user, exc: ValidationError):
    return render(
        request,
        "voters/list.html",
        {
            "user": user,
            "voters": [],
            "total": 0,
            "query": VoterSearch(),
            "error": _form_errors(exc)[0],
            "filter_values": {},
            "parties": PARTIES,
        },
        status_code=422,
    )


@router.get("/voters")
def voter_list(request: Request, db: Session = Depends(get_db), user=Depends(web_user)):
    # Filters are never read from the URL; only pagination is.
    paging = {key: value for key, value in request.query_params.items() if key in {"page", "page_size"}}
    try:
        query = VoterSearch.model_validate(paging)
    except ValidationError as exc:
        return _list_error(request, user, exc)
    return _render_list(request, db, user, query)


@router.post("/voters/search")
async def voter_search(request: Request, db: Session = Depends(get_db), user=Depends(web_user)):
    form = await request.form()
    try:
        query = _search_from_form(form)
    except ValidationError as exc:
        return _list_error(request, user, exc)
    return _render_list(request, db, user, query)


@router.post("/voters/export.csv")
async def export_csv_form(request: Request, db: Session = Depends(get_db), user=Depends(web_user)):
    if user.role != "admin":
        return _forbidden(request, user)
    form = await request.form()
    try:
        query = _search_from_form(form)
    except ValidationError as exc:
        return _list_error(request, user, exc)
    payload = voters_to_csv(export_voters(db, query)).encode("utf-8-sig")
    return Response(
        content=payload,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="nathavaram-voters.csv"'},
    )


@router.post("/voters/export.xlsx")
async def export_xlsx_form(request: Request, db: Session = Depends(get_db), user=Depends(web_user)):
    if user.role != "admin":
        return _forbidden(request, user)
    form = await request.form()
    try:
        query = _search_from_form(form)
    except ValidationError as exc:
        return _list_error(request, user, exc)
    return Response(
        content=voters_to_xlsx(export_voters(db, query)),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="nathavaram-voters.xlsx"'},
    )


@router.get("/voters/new")
def new_voter_form(request: Request, user=Depends(web_user)):
    return render(
        request,
        "voters/form.html",
        {"user": user, "voter": None, "errors": [], "values": {}},
    )


@router.post("/voters/new")
async def new_voter_submit(request: Request, db: Session = Depends(get_db), user=Depends(web_user)):
    form = await request.form()
    raw = _voter_form(form)
    try:
        data = VoterWrite.model_validate(raw)
    except ValidationError as exc:
        return render(
            request,
            "voters/form.html",
            {"user": user, "voter": None, "errors": _form_errors(exc), "values": raw},
            status_code=400,
        )
    try:
        voter = create_voter(db, data, source_for(user))
    except ServiceError as exc:
        return render(
            request,
            "voters/form.html",
            {
                "user": user,
                "voter": None,
                "errors": [f"{LABEL_BY_FIELD.get(exc.field or '', 'Form')}: {exc.message}"],
                "values": raw,
            },
            status_code=exc.status_code,
        )
    return RedirectResponse(f"/voters/{voter.voter_id}/edit?saved=1", status_code=303)


@router.get("/voters/{voter_id}/edit")
def edit_voter_form(
    voter_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(web_user),
):
    try:
        voter = get_active(db, voter_id)
    except ServiceError:
        return render(request, "404.html", {"user": user}, status_code=404)
    values = _stored_values(voter)
    return render(
        request,
        "voters/form.html",
        {
            "user": user,
            "voter": voter,
            "errors": [],
            "values": values,
            "saved": request.query_params.get("saved") == "1",
        },
    )


@router.post("/voters/{voter_id}/edit")
async def edit_voter_submit(
    voter_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(web_user),
):
    form = await request.form()
    raw = _voter_form(form)
    try:
        voter = get_active(db, voter_id)
    except ServiceError:
        return render(request, "404.html", {"user": user}, status_code=404)
    # The form shows VoterId as plain text and never posts it. A crafted post that
    # carries a different value is refused rather than ignored.
    posted_voter_id = form.get("voter_id")
    if posted_voter_id is not None and str(posted_voter_id).strip() != voter.voter_id:
        return render(
            request,
            "voters/form.html",
            {
                "user": user,
                "voter": voter,
                "errors": [f"VoterId: {VOTER_ID_LOCKED_MESSAGE}"],
                "values": {**raw, "voter_id": voter.voter_id},
            },
            status_code=400,
        )
    raw["voter_id"] = voter.voter_id
    privileged = is_privileged(user)
    if not privileged:
        # Locked fields are rendered read-only (selects are disabled and not posted), so a
        # missing one means "unchanged". A posted value that differs is refused by the service.
        stored = _stored_values(voter)
        for field in DATA_FIELDS:
            if field not in USER_EDITABLE_FIELDS and form.get(field) is None:
                raw[field] = str(stored[field])
    try:
        data = VoterWrite.model_validate(raw)
    except ValidationError as exc:
        return render(
            request,
            "voters/form.html",
            {"user": user, "voter": voter, "errors": _form_errors(exc), "values": raw},
            status_code=400,
        )
    try:
        replace_voter(db, voter_id, data, source_for(user), privileged=privileged)
    except ServiceError as exc:
        return render(
            request,
            "voters/form.html",
            {
                "user": user,
                "voter": voter,
                "errors": [f"{LABEL_BY_FIELD.get(exc.field or '', 'Form')}: {exc.message}"],
                "values": raw,
            },
            status_code=exc.status_code,
        )
    return RedirectResponse(f"/voters/{voter.voter_id}/edit?saved=1", status_code=303)


@router.get("/voters/{voter_id}/delete")
def delete_confirm(
    voter_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(web_user),
):
    if user.role != "admin":
        return _forbidden(request, user)
    try:
        voter = get_active(db, voter_id)
    except ServiceError:
        return render(request, "404.html", {"user": user}, status_code=404)
    return render(request, "voters/delete.html", {"user": user, "voter": voter})


@router.post("/voters/{voter_id}/delete")
def delete_submit(
    voter_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(web_user),
):
    if user.role != "admin":
        return _forbidden(request, user)
    try:
        soft_delete(db, voter_id, source_for(user))
    except ServiceError:
        return RedirectResponse("/voters", status_code=303)
    return RedirectResponse("/voters?removed=1", status_code=303)


@router.get("/reports")
def reports(request: Request, db: Session = Depends(get_db), user=Depends(web_user)):
    return render(request, "reports.html", {"user": user, "report": build_report(db)})


@router.get("/master-data")
def master_data_page(
    request: Request,
    tab: str = DEFAULT_MASTER_TAB,
    db: Session = Depends(get_db),
    user=Depends(web_user),
):
    if user.role != "admin":
        return _forbidden(request, user)
    if tab not in ENTITIES:
        tab = DEFAULT_MASTER_TAB
    return render(
        request,
        "master_data.html",
        {"user": user, "tabs": tab_list(), "active": tab, "table": build_master_table(db, tab)},
    )


@router.get("/import")
def import_form(request: Request, user=Depends(web_user)):
    if user.role != "admin":
        return _forbidden(request, user)
    return render(request, "import.html", {"user": user, "report": None, "error": None})


@router.post("/import")
async def import_submit(request: Request, db: Session = Depends(get_db), user=Depends(web_user)):
    if user.role != "admin":
        return _forbidden(request, user)
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        return render(
            request,
            "import.html",
            {"user": user, "report": None, "error": "Choose a CSV file"},
            status_code=400,
        )
    content = await upload.read()
    if len(content) > 5 * 1024 * 1024:
        return render(
            request,
            "import.html",
            {"user": user, "report": None, "error": "CSV larger than 5 MB"},
            status_code=413,
        )
    try:
        report = import_csv(db, content, source_for(user))
    except ImportRejected as exc:
        return render(
            request,
            "import.html",
            {"user": user, "report": None, "error": exc.message},
            status_code=400,
        )
    return render(request, "import.html", {"user": user, "report": report, "error": None})


@router.get("/sample.csv")
def sample_csv(request: Request, user=Depends(web_user)):
    if user.role != "admin":
        return _forbidden(request, user)
    path = ROOT / "sample" / "voters_sample.csv"
    return FileResponse(path, media_type="text/csv; charset=utf-8", filename="voters_sample.csv")


SAVED_MESSAGES = {
    "1": "User created.",
    "reset": "Temporary password set. The user must change it at next sign-in.",
    "deleted": "User deleted.",
}


def _is_owner(user) -> bool:
    return user.role == "admin" and user.is_owner


def _users_page(
    request: Request,
    db: Session,
    user,
    errors: list[str] | None = None,
    saved: str = "",
    status_code: int = 200,
    values: dict | None = None,
):
    return render(
        request,
        "users.html",
        {
            "user": user,
            "users": list_users(db),
            "errors": errors or [],
            "saved_message": SAVED_MESSAGES.get(saved, ""),
            "values": values or {},
        },
        status_code=status_code,
    )


@router.get("/users")
def users_page(request: Request, db: Session = Depends(get_db), user=Depends(web_user)):
    if not _is_owner(user):
        return _forbidden(request, user)
    return _users_page(request, db, user, saved=request.query_params.get("saved", ""))


@router.post("/users")
async def users_submit(request: Request, db: Session = Depends(get_db), user=Depends(web_user)):
    if not _is_owner(user):
        return _forbidden(request, user)
    form = await request.form()
    values = {
        "username": str(form.get("username") or ""),
        "password": str(form.get("password") or ""),
        "role": str(form.get("role") or "user"),
    }
    shown = {"username": values["username"], "role": values["role"]}  # never echo the password
    try:
        data = UserCreate.model_validate(values)
    except ValidationError as exc:
        return _users_page(request, db, user, _form_errors(exc), status_code=400, values=shown)
    try:
        create_user(db, data, request.app.state.settings)
    except ServiceError as exc:
        return _users_page(request, db, user, [exc.message], status_code=exc.status_code, values=shown)
    return RedirectResponse("/users?saved=1", status_code=303)


@router.post("/users/{user_id}/reset-password")
async def users_reset_password(
    user_id: str, request: Request, db: Session = Depends(get_db), user=Depends(web_user)
):
    if not _is_owner(user):
        return _forbidden(request, user)
    form = await request.form()
    try:
        body = ResetPassword.model_validate({"new_password": str(form.get("new_password") or "")})
    except ValidationError as exc:
        return _users_page(request, db, user, _form_errors(exc), status_code=400)
    try:
        reset_user_password(
            db,
            user,
            user_id,
            body.new_password,
            request.app.state.settings,
            request.app.state.login_throttle,
        )
    except ServiceError as exc:
        return _users_page(request, db, user, [exc.message], status_code=exc.status_code)
    return RedirectResponse("/users?saved=reset", status_code=303)


@router.post("/users/{user_id}/delete")
async def users_delete(
    user_id: str, request: Request, db: Session = Depends(get_db), user=Depends(web_user)
):
    if not _is_owner(user):
        return _forbidden(request, user)
    form = await request.form()
    if form.get("confirm") != "yes":
        return _users_page(request, db, user, ["Confirm the deletion first."], status_code=400)
    try:
        delete_user(db, user, user_id)
    except ServiceError as exc:
        return _users_page(request, db, user, [exc.message], status_code=exc.status_code)
    return RedirectResponse("/users?saved=deleted", status_code=303)
