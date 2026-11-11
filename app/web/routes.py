"""HTML pages. They call the same services as the JSON API."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import ROOT
from app.constants import DATA_FIELDS, GENDERS, LABEL_BY_FIELD, PARTIES
from app.database import get_db
from app.deps import resolve_user, source_for
from app.errors import ServiceError
from app.schemas import UserCreate, VoterSearch, VoterWrite
from app.security import create_access_token
from app.services.impex import ImportRejected, import_csv
from app.services.reports import build_report
from app.services.users import authenticate, create_user, list_users
from app.services.voters import create_voter, get_active, replace_voter, search_voters, soft_delete
from app.web.templates import render

router = APIRouter()


class LoginRequired(Exception):
    def __init__(self, next_path: str) -> None:
        self.next_path = next_path


def safe_next(value: str | None) -> str:
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/voters"
    return value


def web_user(request: Request, db: Session = Depends(get_db)):
    user = resolve_user(request, db, None)
    if user is None:
        raise LoginRequired(request.url.path)
    return user


def _form_errors(exc: ValidationError) -> list[str]:
    lines = []
    for err in exc.errors():
        loc = err.get("loc") or ()
        field = str(loc[-1]) if loc else "form"
        label = LABEL_BY_FIELD.get(field, field.replace("_", " "))
        message = err["msg"]
        prefix = "Value error, "
        if message.startswith(prefix):
            message = message[len(prefix) :]
        lines.append(f"{label}: {message}")
    return lines


def _voter_form(form) -> dict:
    return {field: str(form.get(field) or "") for field in DATA_FIELDS}


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
    return RedirectResponse("/voters" if user else "/login", status_code=303)


@router.get("/login")
def login_form(request: Request, db: Session = Depends(get_db)):
    if resolve_user(request, db, None) is not None:
        return RedirectResponse(safe_next(request.query_params.get("next")), status_code=303)
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
    user = authenticate(db, username, password)
    if user is None:
        return render(
            request,
            "login.html",
            {"next": nxt, "error": "Invalid username or password"},
            status_code=401,
        )
    response = RedirectResponse(nxt, status_code=303)
    _set_cookie(response, request, user)
    return response


@router.post("/logout")
def logout(request: Request):
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(request.app.state.settings.cookie_name, path="/")
    return response


@router.get("/voters")
def voter_list(request: Request, db: Session = Depends(get_db), user=Depends(web_user)):
    try:
        query = VoterSearch.model_validate(dict(request.query_params))
    except ValidationError as exc:
        return render(
            request,
            "voters/list.html",
            {
                "user": user,
                "voters": [],
                "total": 0,
                "query": VoterSearch(),
                "error": _form_errors(exc)[0],
                "export_query": "",
            },
            status_code=422,
        )
    rows, total = search_voters(db, query)
    params = {key: value for key, value in request.query_params.items() if key not in {"page", "page_size"}}
    return render(
        request,
        "voters/list.html",
        {
            "user": user,
            "voters": rows,
            "total": total,
            "query": query,
            "error": None,
            "export_query": urlencode(params),
            "genders": GENDERS,
            "parties": PARTIES,
        },
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
    return RedirectResponse(f"/voters/{voter.id}/edit?saved=1", status_code=303)


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
    values = {field: getattr(voter, field) if getattr(voter, field) is not None else "" for field in DATA_FIELDS}
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
        replace_voter(db, voter_id, data, source_for(user))
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
    return RedirectResponse(f"/voters/{voter_id}/edit?saved=1", status_code=303)


@router.get("/voters/{voter_id}/delete")
def delete_confirm(
    voter_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(web_user),
):
    try:
        voter = get_active(db, voter_id)
    except ServiceError:
        return render(request, "404.html", {"user": user}, status_code=404)
    return render(request, "voters/delete.html", {"user": user, "voter": voter})


@router.post("/voters/{voter_id}/delete")
def delete_submit(
    voter_id: str,
    db: Session = Depends(get_db),
    user=Depends(web_user),
):
    try:
        soft_delete(db, voter_id, source_for(user))
    except ServiceError:
        return RedirectResponse("/voters", status_code=303)
    return RedirectResponse("/voters?removed=1", status_code=303)


@router.get("/reports")
def reports(request: Request, db: Session = Depends(get_db), user=Depends(web_user)):
    return render(request, "reports.html", {"user": user, "report": build_report(db)})


@router.get("/import")
def import_form(request: Request, user=Depends(web_user)):
    return render(request, "import.html", {"user": user, "report": None, "error": None})


@router.post("/import")
async def import_submit(request: Request, db: Session = Depends(get_db), user=Depends(web_user)):
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
def sample_csv(user=Depends(web_user)):
    _ = user
    path = ROOT / "sample" / "voters_sample.csv"
    return FileResponse(path, media_type="text/csv; charset=utf-8", filename="voters_sample.csv")


@router.get("/users")
def users_page(request: Request, db: Session = Depends(get_db), user=Depends(web_user)):
    if user.role != "admin":
        return render(request, "403.html", {"user": user}, status_code=403)
    return render(
        request,
        "users.html",
        {"user": user, "users": list_users(db), "errors": [], "saved": request.query_params.get("saved") == "1"},
    )


@router.post("/users")
async def users_submit(request: Request, db: Session = Depends(get_db), user=Depends(web_user)):
    if user.role != "admin":
        return render(request, "403.html", {"user": user}, status_code=403)
    form = await request.form()
    try:
        data = UserCreate.model_validate(
            {
                "username": str(form.get("username") or ""),
                "password": str(form.get("password") or ""),
                "role": str(form.get("role") or "user"),
            }
        )
    except ValidationError as exc:
        return render(
            request,
            "users.html",
            {"user": user, "users": list_users(db), "errors": _form_errors(exc), "saved": False},
            status_code=400,
        )
    try:
        create_user(db, data, request.app.state.settings)
    except ServiceError as exc:
        return render(
            request,
            "users.html",
            {"user": user, "users": list_users(db), "errors": [exc.message], "saved": False},
            status_code=exc.status_code,
        )
    return RedirectResponse("/users?saved=1", status_code=303)
