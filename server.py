"""EPITrack web app (FastAPI + Jinja2)

รัน:  .venv\\Scripts\\python.exe -m uvicorn server:app --host 0.0.0.0 --port 8701
"""

import csv
import io
import json
import secrets
from datetime import date, timedelta
from urllib.parse import quote, urlencode

from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import backup
import db
import history
import jhcis
import schedule
import store
import updates
from paths import DATA_DIR, PORTS, RESOURCE_DIR, UPDATE_MANIFEST_ID, UPDATE_ZIP_ID, VERSION

THAI_MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
               "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
SEX = {"1": "ชาย", "2": "หญิง"}
ACTIONS = {"create": "เพิ่ม", "update": "แก้ไข", "delete": "ลบ/ยกเลิก", "reset": "คืนค่าเริ่มต้น", "password": "เปลี่ยนรหัสผ่าน"}
PENDING_STATUS = {"missing": ("ยังไม่พบใน JHCIS", "danger"), "mismatch": ("วันที่ไม่ตรง", "warn")}
# หน้าที่เปิดได้โดยไม่ต้องล็อกอิน
PUBLIC = ("/static/", "/login", "/setup", "/settings", "/api/ping", "/api/status", "/update/")


def session_key() -> str:
    path = DATA_DIR / "session.key"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        key = secrets.token_hex(32)
        path.write_text(key, encoding="utf-8")
        return key


app = FastAPI(docs_url=None, redoc_url=None)
updates.check_in_background()  # เช็กเวอร์ชันใหม่ทุกครั้งที่เปิดโปรแกรม (เช็กไม่ได้ = เงียบไว้)
backup.start_daily()
app.mount("/static", StaticFiles(directory=RESOURCE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=RESOURCE_DIR / "templates")


# ---------- ตัวช่วยแสดงผล ----------
def thai_date(value, short: bool = True) -> str:
    if not value:
        return "-"
    if isinstance(value, str):
        value = date.fromisoformat(value[:10])
    year = value.year + 543
    return f"{value.day} {THAI_MONTHS[value.month - 1]} {year % 100 if short else year}"


def age_text(birth, on=None) -> str:
    if not birth:
        return "-"
    y, m, d = schedule.age_parts(birth, on or date.today())
    if y >= 3:
        return f"{y} ปี" + (f" {m} ด." if m else "")
    if y or m:
        return f"{y * 12 + m} เดือน" + (f" {d} วัน" if y * 12 + m < 6 and d else "")
    return f"{d} วัน"


def mask_cid(cid: str | None) -> str:
    if not cid or len(cid) != 13:
        return cid or "-"
    return f"{cid[0]}-{cid[1:5]}-xxxxx-{cid[10:12]}-{cid[12]}"


def measure(value) -> str:
    """ค่าที่ชั่งวัด ตัดศูนย์ท้ายทศนิยม (13.70 -> 13.7) ไม่ได้วัด = ว่าง"""
    if value is None or value == "":
        return ""
    return f"{float(value):.2f}".rstrip("0").rstrip(".")


templates.env.filters.update(thai_date=thai_date, age=age_text, cid=mask_cid, measure=measure,
                             sex=lambda v: SEX.get(str(v or ""), "-"))
templates.env.globals.update(STATUS=schedule.STATUS, PLACES=store.PLACES, ROLES=store.ROLES, MEASURES=store.MEASURES,
                             ACTIONS=ACTIONS, AGE_BANDS=schedule.AGE_BANDS, version=VERSION)


def render(request: Request, name: str, status_code: int = 200, **context):
    context.setdefault("user", getattr(request.state, "user", None))
    context.setdefault("config", db.load_config())
    context.setdefault("msg", request.query_params.get("msg", ""))
    context.setdefault("err", request.query_params.get("err", ""))
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def back(url: str, msg: str = "", err: str = "", anchor: str = "") -> RedirectResponse:
    query = urlencode({k: v for k, v in (("msg", msg), ("err", err)) if v})
    sep = "&" if "?" in url else "?"
    return RedirectResponse(f"{url}{sep + query if query else ''}{'#' + anchor if anchor else ''}", status_code=303)


def parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def person_url(pcu: str, pid) -> str:
    return f"/person/{pcu}/{pid}"


def is_local(request: Request) -> bool:
    return request.client is not None and request.client.host in ("127.0.0.1", "::1", "localhost")


# ---------- ล็อกอินและการเริ่มใช้งาน ----------
_schema_ready = {"ok": False}


def ensure_ready() -> str | None:
    """ตรวจว่าตั้งค่าแล้ว และฐานข้อมูล epitrack พร้อม คืนข้อความผิดพลาดถ้าไม่พร้อม"""
    if not db.is_configured():
        return "ยังไม่ได้ตั้งค่าการเชื่อมต่อ"
    if _schema_ready["ok"]:
        return None
    try:
        store.ensure_schema()
    except Exception as exc:
        return db.explain_error(exc, db.load_config()) if isinstance(exc, db.pymysql.MySQLError) else str(exc)
    _schema_ready["ok"] = True
    return None


def db_down(request: Request, exc: Exception):
    """ฐานข้อมูลหลุดระหว่างใช้งาน: บอกสาเหตุ แล้วตรวจ schema ใหม่ในคำขอถัดไป"""
    _schema_ready["ok"] = False
    return render(request, "message.html", 503, title="เชื่อมต่อฐานข้อมูลไม่ได้",
                  text=f"{db.explain_error(exc, db.load_config())} ลองใหม่อีกครั้ง หรือแจ้งผู้ดูแล")


@app.exception_handler(db.pymysql.MySQLError)
async def mysql_error(request: Request, exc: Exception):
    return db_down(request, exc)


@app.middleware("http")
async def auth(request: Request, call_next):
    path = request.url.path
    request.state.user = None
    if path.startswith("/static/") or path in ("/api/ping",):
        return await call_next(request)
    problem = ensure_ready()
    if problem:
        request.state.setup_problem = problem
        if path.startswith(("/settings", "/api/status", "/update/")):
            return await call_next(request)
        return RedirectResponse("/settings", status_code=303)
    request.state.setup_problem = None
    try:
        user_id = request.session.get("user_id")
        user = store.get_user(user_id) if user_id else None
        no_users = user is None and store.user_count() == 0
    except db.pymysql.MySQLError as exc:
        return db_down(request, exc)
    if user and user["active"]:
        request.state.user = user
    elif user_id:
        request.session.clear()
    if request.state.user is None and not path.startswith(PUBLIC):
        if no_users:
            return RedirectResponse("/setup", status_code=303)
        return RedirectResponse(f"/login?next={quote(path)}", status_code=303)
    if path.startswith("/admin") and request.state.user["role"] != "admin":
        return render(request, "message.html", 403, title="ไม่มีสิทธิ์",
                      text="หน้านี้สำหรับผู้ดูแลเท่านั้น")
    return await call_next(request)


# SessionMiddleware ต้องอยู่นอกสุด (เพิ่มทีหลัง = ทำงานก่อน) ให้ middleware auth อ่าน session ได้
app.add_middleware(SessionMiddleware, secret_key=session_key(), session_cookie="epitrack",
                   max_age=12 * 3600, same_site="lax")


@app.get("/login")
def login_page(request: Request, next: str = "/"):
    if store.user_count() == 0:
        return RedirectResponse("/setup", status_code=303)
    return render(request, "login.html", title="เข้าสู่ระบบ", next=next, bare=True)


@app.post("/login")
def login(request: Request, username: str = Form(""), password: str = Form(""), next: str = Form("/")):
    user = store.authenticate(username, password)
    if not user:
        return render(request, "login.html", 401, title="เข้าสู่ระบบ", next=next, bare=True,
                      err="ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง", username=username)
    request.session.clear()
    request.session["user_id"] = user["id"]
    return RedirectResponse(next if next.startswith("/") and not next.startswith("//") else "/", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/setup")
def setup_page(request: Request):
    if store.user_count() > 0:
        return RedirectResponse("/login", status_code=303)
    return render(request, "setup.html", title="สร้างผู้ดูแลคนแรก", bare=True, form={})


@app.post("/setup")
def setup(request: Request, username: str = Form(""), display_name: str = Form(""),
          password: str = Form(""), password2: str = Form("")):
    if store.user_count() > 0:
        return RedirectResponse("/login", status_code=303)
    form = {"username": username, "display_name": display_name}
    if password != password2:
        return render(request, "setup.html", 400, title="สร้างผู้ดูแลคนแรก", bare=True, form=form,
                      err="รหัสผ่านทั้งสองช่องไม่ตรงกัน")
    try:
        user_id = store.create_user(username, display_name, password, "admin", None)
    except ValueError as exc:
        return render(request, "setup.html", 400, title="สร้างผู้ดูแลคนแรก", bare=True, form=form, err=str(exc))
    request.session["user_id"] = user_id
    return back("/", msg="สร้างบัญชีผู้ดูแลแล้ว เพิ่มบัญชีเจ้าหน้าที่ได้ในหน้า \"ผู้ใช้\"")


@app.get("/account")
def account_page(request: Request):
    return render(request, "account.html", title="บัญชีของฉัน", active="account")


@app.post("/account")
def account_save(request: Request, old: str = Form(""), new: str = Form(""), new2: str = Form("")):
    if new != new2:
        return back("/account", err="รหัสผ่านใหม่ทั้งสองช่องไม่ตรงกัน")
    try:
        store.change_own_password(request.state.user["id"], old, new)
    except ValueError as exc:
        return back("/account", err=str(exc))
    return back("/account", msg="เปลี่ยนรหัสผ่านแล้ว")


# ---------- หน้าหลัก / ค้นหา ----------
@app.get("/")
def home(request: Request, q: str = ""):
    results = jhcis.search(q) if q.strip() else []
    today = date.today()
    upcoming = store.query(
        "SELECT pcucodeperson, pid, date_appoint, vaccines FROM appointments "
        "WHERE deleted_at IS NULL AND date_appoint BETWEEN %s AND %s ORDER BY date_appoint",
        (today, today + timedelta(days=7)),
    )
    people = jhcis.persons([(a["pcucodeperson"], a["pid"]) for a in upcoming])
    for a in upcoming:
        a["person"] = people.get((a["pcucodeperson"], a["pid"]))
    span = history.default_pending_range(today)
    pending = len(history.pending_jhcis(*span)) + len(history.pending_growth(*span))
    return render(request, "home.html", title="ค้นหาเด็ก", active="home", q=q, results=results,
                  upcoming=upcoming, pending_count=pending, in_area=jhcis.in_area, today=today)


# ---------- รายชื่อเด็ก ----------
def roster_cards(band: str, villcodes: list[str], status: str, today: date) -> list[history.Card]:
    if band not in schedule.AGE_BANDS:
        band = "0"
    born_from, born_to = schedule.birth_range(band, today)
    cards = history.roster(jhcis.children(born_from, born_to, villcodes or None), today)
    if status == "pending":
        cards = [c for c in cards if c.pending]
    elif status == "overdue":
        cards = [c for c in cards if c.worst == "overdue"]
    return cards


def roster_params(request: Request) -> tuple[str, list[str], str]:
    qp = request.query_params
    band = qp.get("band", "0")
    status = qp.get("status", "pending")
    return (band if band in schedule.AGE_BANDS else "0", qp.getlist("village"),
            status if status in ("all", "pending", "overdue") else "pending")


@app.get("/children")
def children_page(request: Request):
    band, villcodes, status = roster_params(request)
    today = date.today()
    cards = roster_cards(band, villcodes, status, today)
    appts = store.next_appointments([(c.person["pcucodeperson"], c.person["pid"]) for c in cards], today)
    export = "/children/export?" + urlencode([("band", band), ("status", status)] + [("village", v) for v in villcodes])
    return render(request, "children.html", title="รายชื่อเด็กและสถานะวัคซีน", active="children",
                  cards=cards, band=band, villcodes=villcodes, status=status, villages=jhcis.villages(),
                  appts=appts, export_url=export, today=today)


@app.get("/children/export")
def children_export(request: Request):
    band, villcodes, status = roster_params(request)
    today = date.today()
    cards = roster_cards(band, villcodes, status, today)
    appts = store.next_appointments([(c.person["pcucodeperson"], c.person["pid"]) for c in cards], today)
    out = io.StringIO()
    out.write("﻿")  # ให้ Excel อ่านภาษาไทยได้
    w = csv.writer(out)
    w.writerow(["หมู่", "บ้านเลขที่", "ชื่อ", "HN", "เลขบัตรประชาชน", "วันเกิด", "อายุ", "เพศ", "มารดา", "โทร",
                "สถานะ", "วัคซีนที่ค้าง", "นัดครั้งถัดไป"])
    for c in cards:
        p = c.person
        owed = ", ".join(f"{s.label} ({s.status_text})" for _, s in c.pending)
        w.writerow([p["villno"], p["hno"], p["name"], p["pid"], p["idcard"], p["birth"], age_text(p["birth"], today),
                    SEX.get(str(p["sex"]), ""), p["mother"], p["phone"],
                    {"overdue": "เลยกำหนด", "due": "ถึงกำหนด", "ok": "ครบตามอายุ"}[c.worst], owed,
                    appts.get((p["pcucodeperson"], p["pid"]), "")])
    filename = f"epitrack_{band}_{today}.csv"
    return StreamingResponse(iter([out.getvalue().encode("utf-8")]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ---------- สมุดวัคซีนรายคน ----------
def load_person(pcu: str, pid: int) -> dict:
    person = jhcis.person(pcu, pid)
    if not person:
        raise store.NotFound("ไม่พบคนนี้ใน JHCIS")
    return person


def vaccine_options(rules: schedule.Rules, vmap: dict) -> list[dict]:
    """รหัสวัคซีนที่เลือกได้ตอนบันทึก: เฉพาะรหัสที่นับเป็นชุดวัคซีน เรียงตามลำดับในตาราง"""
    order = {s.id: i for i, s in enumerate(rules.series)}
    names = {s.id: s.name for s in rules.series}
    opts = []
    for v in jhcis.vaccines():
        series = [s for s, _ in vmap.get(v["code"], []) if s in order]
        if series:
            opts.append({**v, "series": series, "group": names[series[0]], "order": order[series[0]]})
    return sorted(opts, key=lambda o: (o["order"], o["code"]))


@app.get("/person/{pcu}/{pid}")
def person_page(request: Request, pcu: str, pid: int, edit: int = 0, gedit: int = 0):
    try:
        person = load_person(pcu, pid)
    except store.NotFound as exc:
        return render(request, "message.html", 404, title="ไม่พบข้อมูล", text=str(exc))
    today = date.today()
    rules, vmap = store.rules(), store.vaccine_map()
    names = {v["code"]: v["name"] for v in jhcis.vaccines()}
    c = history.card(person, today, rules, vmap, names)
    suggest_date, suggest_items = schedule.suggest_appointment(c.results, today)
    editing = store.get_vaccination(edit) if edit else None
    if editing and (editing["pcucodeperson"], editing["pid"]) != (pcu, pid):
        editing = None
    gediting = store.get_growth(gedit) if gedit else None
    if gediting and (gediting["pcucodeperson"], gediting["pid"]) != (pcu, pid):
        gediting = None
    return render(request, "person.html", title=person["name"], active="children", c=c, p=person,
                  options=vaccine_options(rules, vmap), today=today, in_area=jhcis.in_area(person),
                  suggest_date=suggest_date, suggest_items=suggest_items,
                  appointments=store.appointments_for(pcu, pid), jhcis_appointments=jhcis.epi_appointments(pcu, pid),
                  editing=editing, default_giver=request.state.user["display_name"],
                  growth=history.growth(pcu, pid), gediting=gediting)


def vaccination_form(form) -> dict:
    return {
        "vaccine_code": form.get("vaccine_code", ""),
        "date_given": parse_date(form.get("date_given")),
        "lot_no": form.get("lot_no", ""),
        "expire_date": parse_date(form.get("expire_date")),
        "manufacturer": form.get("manufacturer", ""),
        "place": form.get("place", ""),
        "place_note": form.get("place_note", ""),
        "giver": form.get("giver", ""),
        "note": form.get("note", ""),
    }


def growth_form(form, prefix: str = "", **override) -> dict:
    data = {k: form.get(prefix + k, "") for k in store.MEASURES}
    data.update(date_checked=parse_date(form.get("date_checked")), place=form.get("place", ""),
                note=form.get(prefix + "note", ""))
    data.update(override)
    return data


@app.post("/person/{pcu}/{pid}/vaccinations")
async def vaccination_add(request: Request, pcu: str, pid: int):
    """บันทึกการฉีด และการชั่งวัดวันเดียวกันถ้ากรอกมา (ตรวจค่าชั่งวัดก่อน เพื่อไม่ให้บันทึกครึ่งเดียว)"""
    form = await request.form()
    data = vaccination_form(form)
    measured = growth_form(form, "g_", date_checked=data["date_given"], place=data["place"], note="")
    try:
        load_person(pcu, pid)
        if store.has_measures(measured):
            store.clean_growth(measured)
        store.add_vaccination(pcu, pid, data, request.state.user["id"])
    except (ValueError, store.NotFound) as exc:
        return back(person_url(pcu, pid), err=str(exc), anchor="record")
    if store.has_measures(measured):
        try:
            store.add_growth(pcu, pid, measured, request.state.user["id"])
        except ValueError as exc:
            return back(person_url(pcu, pid), msg="บันทึกการฉีดแล้ว", err=f"ไม่ได้บันทึกการชั่งวัด: {exc}",
                        anchor="growth")
        return back(person_url(pcu, pid), msg="บันทึกการฉีดและการชั่งวัดแล้ว", anchor="records")
    return back(person_url(pcu, pid), msg="บันทึกการฉีดแล้ว", anchor="records")


@app.post("/vaccinations/{record_id}/edit")
async def vaccination_edit(request: Request, record_id: int):
    form = await request.form()
    try:
        row = store.get_vaccination(record_id)
        url = person_url(row["pcucodeperson"], row["pid"])
        store.update_vaccination(record_id, vaccination_form(form), form.get("reason", ""), request.state.user["id"])
    except store.NotFound as exc:
        return back("/", err=str(exc))
    except ValueError as exc:
        return back(f"{url}?edit={record_id}", err=str(exc), anchor="record")
    return back(url, msg="แก้ไขรายการฉีดแล้ว", anchor="records")


@app.post("/vaccinations/{record_id}/delete")
def vaccination_delete(request: Request, record_id: int, reason: str = Form("")):
    try:
        row = store.get_vaccination(record_id)
        store.hide_vaccination(record_id, reason, request.state.user["id"])
    except store.NotFound as exc:
        return back("/", err=str(exc))
    except ValueError as exc:
        return back(person_url(row["pcucodeperson"], row["pid"]), err=str(exc), anchor="records")
    return back(person_url(row["pcucodeperson"], row["pid"]), msg="ลบรายการฉีดแล้ว (ดูย้อนหลังได้ในประวัติการแก้ไข)",
                anchor="records")


@app.get("/vaccinations/{record_id}/history")
def vaccination_history(request: Request, record_id: int):
    try:
        row = store.get_vaccination(record_id)
    except store.NotFound as exc:
        return render(request, "message.html", 404, title="ไม่พบข้อมูล", text=str(exc))
    person = jhcis.person(row["pcucodeperson"], row["pid"])
    return render(request, "record_history.html", title="ประวัติการแก้ไขรายการฉีด", active="children",
                  subject=f"{row['vaccine_code']} วันที่ {thai_date(row['date_given'], False)}",
                  row=row, p=person, logs=store.audit_for("vaccination", record_id))


# ---------- การชั่งวัด ----------
@app.post("/person/{pcu}/{pid}/growth")
async def growth_add(request: Request, pcu: str, pid: int):
    form = await request.form()
    try:
        load_person(pcu, pid)
        store.add_growth(pcu, pid, growth_form(form), request.state.user["id"])
    except (ValueError, store.NotFound) as exc:
        return back(person_url(pcu, pid), err=str(exc), anchor="growth")
    return back(person_url(pcu, pid), msg="บันทึกการชั่งวัดแล้ว", anchor="growth")


@app.post("/growth/{record_id}/edit")
async def growth_edit(request: Request, record_id: int):
    form = await request.form()
    try:
        row = store.get_growth(record_id)
        url = person_url(row["pcucodeperson"], row["pid"])
        store.update_growth(record_id, growth_form(form), form.get("reason", ""), request.state.user["id"])
    except store.NotFound as exc:
        return back("/", err=str(exc))
    except ValueError as exc:
        return back(f"{url}?gedit={record_id}", err=str(exc), anchor="growth")
    return back(url, msg="แก้ไขการชั่งวัดแล้ว", anchor="growth")


@app.post("/growth/{record_id}/delete")
def growth_delete(request: Request, record_id: int, reason: str = Form("")):
    try:
        row = store.get_growth(record_id)
        store.hide_growth(record_id, reason, request.state.user["id"])
    except store.NotFound as exc:
        return back("/", err=str(exc))
    except ValueError as exc:
        return back(person_url(row["pcucodeperson"], row["pid"]), err=str(exc), anchor="growth")
    return back(person_url(row["pcucodeperson"], row["pid"]), msg="ลบการชั่งวัดแล้ว", anchor="growth")


@app.get("/growth/{record_id}/history")
def growth_history(request: Request, record_id: int):
    try:
        row = store.get_growth(record_id)
    except store.NotFound as exc:
        return render(request, "message.html", 404, title="ไม่พบข้อมูล", text=str(exc))
    person = jhcis.person(row["pcucodeperson"], row["pid"])
    return render(request, "record_history.html", title="ประวัติการแก้ไขการชั่งวัด", active="children",
                  subject=f"การชั่งวัดวันที่ {thai_date(row['date_checked'], False)}",
                  row=row, p=person, logs=store.audit_for("growth", record_id))


# ---------- นัด ----------
@app.post("/person/{pcu}/{pid}/appointments")
def appointment_add(request: Request, pcu: str, pid: int, date_appoint: str = Form(""),
                    vaccines: str = Form(""), note: str = Form("")):
    when = parse_date(date_appoint)
    if not when:
        return back(person_url(pcu, pid), err="ใส่วันนัด", anchor="appointments")
    try:
        new_id = store.add_appointment(pcu, pid, when, vaccines, note, request.state.user["id"])
    except ValueError as exc:
        return back(person_url(pcu, pid), err=str(exc), anchor="appointments")
    return back(person_url(pcu, pid), msg=f"บันทึกนัดวันที่ {thai_date(when, False)} แล้ว",
                anchor=f"appt-{new_id}")


@app.post("/appointments/{appointment_id}/cancel")
def appointment_cancel(request: Request, appointment_id: int, reason: str = Form("")):
    try:
        row = store.cancel_appointment(appointment_id, reason, request.state.user["id"])
    except store.NotFound as exc:
        return back("/", err=str(exc))
    return back(person_url(row["pcucodeperson"], row["pid"]), msg="ยกเลิกนัดแล้ว", anchor="appointments")


@app.get("/appointments/{appointment_id}/slip")
def appointment_slip(request: Request, appointment_id: int):
    try:
        appt = store.get_appointment(appointment_id)
        person = load_person(appt["pcucodeperson"], appt["pid"])
    except store.NotFound as exc:
        return render(request, "message.html", 404, title="ไม่พบข้อมูล", text=str(exc))
    return render(request, "slip.html", title="ใบนัดฉีดวัคซีน", bare=True, a=appt, p=person,
                  unit=jhcis.unit_name(appt["pcucodeperson"]))


# ---------- รอลง JHCIS ----------
@app.get("/pending")
def pending_page(request: Request, start: str = "", end: str = ""):
    today = date.today()
    d_start, d_end = history.default_pending_range(today)
    d_start, d_end = parse_date(start) or d_start, parse_date(end) or d_end
    items = history.pending_jhcis(d_start, d_end)
    names = {v["code"]: v["name"] for v in jhcis.vaccines()}
    return render(request, "pending.html", title="รอลง JHCIS", active="pending", items=items, start=d_start,
                  end=d_end, names=names, PENDING_STATUS=PENDING_STATUS,
                  growth_items=history.pending_growth(d_start, d_end))


# ---------- ผู้ดูแล ----------
@app.get("/admin/users")
def users_page(request: Request):
    return render(request, "users.html", title="ผู้ใช้", active="users", users=store.list_users())


@app.post("/admin/users")
def users_create(request: Request, username: str = Form(""), display_name: str = Form(""),
                 password: str = Form(""), role: str = Form("staff")):
    try:
        store.create_user(username, display_name, password, role, request.state.user["id"])
    except ValueError as exc:
        return back("/admin/users", err=str(exc))
    return back("/admin/users", msg=f"เพิ่มผู้ใช้ {username.strip()} แล้ว")


@app.post("/admin/users/{user_id}")
def users_update(request: Request, user_id: int, display_name: str = Form(""), role: str = Form("staff"),
                 active: str = Form(""), password: str = Form("")):
    try:
        store.update_user(user_id, display_name, role, active == "1", password, request.state.user["id"])
    except (ValueError, store.NotFound) as exc:
        return back("/admin/users", err=str(exc))
    return back("/admin/users", msg="บันทึกผู้ใช้แล้ว")


@app.get("/admin/schedule")
def schedule_page(request: Request):
    text, is_default = store.schedule_json()
    rules = schedule.parse(json.loads(text))
    return render(request, "schedule.html", title="ตารางวัคซีน", active="schedule", text=text,
                  is_default=is_default, rules=rules, logs=store.audit_for("schedule", "-")[:10])


@app.post("/admin/schedule")
def schedule_save(request: Request, text: str = Form(""), reason: str = Form(""), action: str = Form("save")):
    if not reason.strip():
        return back("/admin/schedule", err="ใส่เหตุผลของการแก้ไขตารางวัคซีน")
    try:
        if action == "reset":
            store.save_schedule(None, request.state.user["id"], reason)
        else:
            json.loads(text)
            store.save_schedule(text, request.state.user["id"], reason)
    except json.JSONDecodeError as exc:
        return back("/admin/schedule", err=f"รูปแบบ JSON ผิดที่บรรทัด {exc.lineno} คอลัมน์ {exc.colno}: {exc.msg}")
    except ValueError as exc:
        return back("/admin/schedule", err=str(exc))
    return back("/admin/schedule", msg="บันทึกตารางวัคซีนแล้ว" if action != "reset" else "กลับไปใช้ตารางเริ่มต้นแล้ว")


@app.get("/admin/map")
def map_page(request: Request):
    rules = store.rules()
    rows = store.map_rows()
    mapped = {r["code"] for r in rows}
    names = {v["code"]: v["name"] for v in jhcis.vaccines()}
    used = jhcis.used_codes(schedule.Age(years=19).before(date.today()))
    unmapped = [u for u in used if u["code"] not in mapped]
    return render(request, "map.html", title="mapping รหัสวัคซีน", active="map", rows=rows, names=names,
                  unmapped=unmapped, series={s.id: s.name for s in rules.series})


@app.post("/admin/map")
def map_save(request: Request, code: str = Form(""), series: str = Form(""), product: str = Form(""),
             action: str = Form("save")):
    code, series = code.strip(), series.strip()
    if not code:
        return back("/admin/map", err="ใส่รหัสวัคซีน")
    try:
        if action == "delete":
            store.delete_map_row(code, series, request.state.user["id"])
            return back("/admin/map", msg=f"ลบ {code} ออกจาก mapping แล้ว")
        if series and not store.rules().get(series):
            raise ValueError(f"ไม่มีชุดวัคซีน {series} ในตารางวัคซีน")
        store.save_map_row(code, series, product.strip() or None, request.state.user["id"])
    except (ValueError, store.NotFound) as exc:
        return back("/admin/map", err=str(exc))
    return back("/admin/map", msg=f"บันทึก {code} แล้ว", anchor=f"code-{code}")


@app.get("/admin/audit")
def audit_page(request: Request):
    return render(request, "audit.html", title="ประวัติการแก้ไข", active="audit", logs=store.recent_audit())


@app.post("/admin/backup")
def backup_now(request: Request):
    try:
        path = backup.run()
    except Exception as exc:
        return back("/settings", err=f"สำรองไม่สำเร็จ: {exc}", anchor="backup")
    return back("/settings", msg=f"สำรองข้อมูลแล้ว: {path.name}", anchor="backup")


# ---------- ตั้งค่าการเชื่อมต่อ ----------
def can_configure(request: Request) -> bool:
    """ผู้ดูแลเสมอ หรือเครื่องที่รันโปรแกรมเอง (localhost) ระหว่างที่ยังใช้งานไม่ได้"""
    user = request.state.user
    if user and user["role"] == "admin":
        return True
    if not is_local(request):
        return False
    if getattr(request.state, "setup_problem", None):
        return True
    try:
        return store.user_count() == 0
    except Exception:
        return True


def update_context(umsg: str = "", uerr: str = "") -> dict:
    config = db.load_config()
    return dict(update=updates.status(), update_msg=umsg, update_err=uerr,
                update_ids={"manifest": config["update_manifest_id"], "zip": config["update_zip_id"]},
                update_builtin=all((UPDATE_MANIFEST_ID, UPDATE_ZIP_ID)))


def settings_context(request: Request) -> dict:
    return dict(title="ตั้งค่า", active="settings", charsets=db.CHARSETS,
                problem=getattr(request.state, "setup_problem", None),
                backups=backup.list_backups()[:10], backup_dir=backup.backup_dir(), backup_status=backup.status(),
                **update_context(request.query_params.get("umsg", ""), request.query_params.get("uerr", "")))


@app.get("/settings")
def settings(request: Request):
    if not can_configure(request):
        return render(request, "message.html", 403, title="ไม่มีสิทธิ์",
                      text="หน้าตั้งค่าสำหรับผู้ดูแล หรือเปิดจากเครื่องที่รันโปรแกรมระหว่างตั้งค่าครั้งแรก")
    return render(request, "settings.html", form=db.load_config(), result=None, **settings_context(request))


@app.post("/settings")
def settings_post(request: Request, action: str = Form(...), host: str = Form(""), port: int = Form(3333),
                  user: str = Form(""), password: str = Form(""), database: str = Form(""),
                  charset: str = Form("utf8"), app_database: str = Form(""), backup_dir: str = Form("")):
    if not can_configure(request):
        return render(request, "message.html", 403, title="ไม่มีสิทธิ์", text="หน้าตั้งค่าสำหรับผู้ดูแลเท่านั้น")
    saved = db.load_config()
    form = dict(saved, host=host.strip(), port=port, user=user.strip(), password=password or saved["password"],
                database=database.strip(), charset=charset if charset in db.CHARSETS else "utf8",
                app_database=app_database.strip() or "epitrack", backup_dir=backup_dir.strip())
    if not form["host"] or not form["user"] or not form["database"]:
        result = (False, "กรุณากรอก IP Address, Username และชื่อฐานข้อมูล JHCIS ให้ครบ")
    elif not db.valid_db_name(form["app_database"]):
        result = (False, "ชื่อฐานข้อมูลของแอปใช้ได้เฉพาะ A-Z a-z 0-9 _")
    elif action == "save":
        ok, message = db.test_connection(form)
        if not ok:
            result = (False, f"ยังไม่บันทึก: {message}")
        else:
            try:
                store.ensure_schema(form)
                db.save_config(form)
            except OSError as exc:
                result = (False, f"บันทึกไม่สำเร็จ: เขียนไฟล์ {db.CONFIG_PATH} ไม่ได้ ({exc.strerror or exc})")
            except Exception as exc:
                msg = db.explain_error(exc, form) if isinstance(exc, db.pymysql.MySQLError) else str(exc)
                result = (False, f"สร้างฐานข้อมูล {form['app_database']} ไม่ได้: {msg}")
            else:
                _schema_ready["ok"] = False
                return back("/settings", msg="บันทึกค่าการเชื่อมต่อแล้ว ฐานข้อมูลของแอปพร้อมใช้งาน")
    else:
        result = db.test_connection(form)
    return render(request, "settings.html", form=form, result=result, **settings_context(request))


# ---------- อัปเดตโปรแกรม ----------
def settings_redirect(msg: str = "", err: str = "") -> RedirectResponse:
    query = urlencode({k: v for k, v in (("umsg", msg), ("uerr", err)) if v})
    return RedirectResponse(f"/settings?{query}#update", status_code=303)


def admin_only(request: Request) -> bool:
    return bool(request.state.user and request.state.user["role"] == "admin") or can_configure(request)


@app.get("/api/update")
def api_update():
    return updates.status()


@app.post("/update/check")
def update_check(request: Request):
    if not admin_only(request):
        return settings_redirect(err="สำหรับผู้ดูแลเท่านั้น")
    s = updates.check()
    if s["error"]:
        return settings_redirect(err=s["error"])
    if s["available"]:
        return settings_redirect(msg=f"มีเวอร์ชันใหม่ v{s['latest']}")
    return settings_redirect(msg=f"ใช้เวอร์ชันล่าสุดอยู่แล้ว (v{s['current']})")


@app.post("/update/dismiss")
def update_dismiss():
    updates.dismiss()
    return JSONResponse({"ok": True})


@app.post("/update/source")
def update_source(request: Request, manifest_id: str = Form(""), zip_id: str = Form("")):
    if not admin_only(request):
        return settings_redirect(err="สำหรับผู้ดูแลเท่านั้น")
    config = db.load_config()
    config.update(update_manifest_id=manifest_id.strip(), update_zip_id=zip_id.strip())
    try:
        db.save_config(config)
    except OSError as exc:
        return settings_redirect(err=f"บันทึกไม่สำเร็จ ({exc.strerror or exc})")
    return settings_redirect(msg="บันทึกแหล่งอัปเดตแล้ว")


def updating_page(request: Request, target: str, action: str):
    return render(request, "updating.html", title="กำลังอัปเดต", active="updating",
                  target=target, action=action, ports=list(PORTS))


@app.post("/update/install")
def update_install(request: Request):
    if not admin_only(request):
        return settings_redirect(err="สำหรับผู้ดูแลเท่านั้น")
    try:
        zip_path = updates.download()
        target = updates.status()["latest"]
        updates.launch_updater("install", zip_path)
    except updates.UpdateError as exc:
        return settings_redirect(err=str(exc))
    return updating_page(request, target, "อัปเดตเป็น")


@app.post("/update/rollback")
def update_rollback(request: Request):
    if not admin_only(request):
        return settings_redirect(err="สำหรับผู้ดูแลเท่านั้น")
    target = updates.previous_version()
    if not target:
        return settings_redirect(err="ไม่มีเวอร์ชันก่อนหน้าให้ย้อนกลับ")
    try:
        updates.launch_updater("rollback")
    except updates.UpdateError as exc:
        return settings_redirect(err=str(exc))
    return updating_page(request, target, "ย้อนกลับเป็น")


# ---------- API ----------
@app.get("/api/ping")
def api_ping():
    """ให้ตัวเปิดโปรแกรมรู้ว่า EPITrack ทำงานอยู่ที่พอร์ตนี้แล้ว"""
    return JSONResponse({"app": "EPITrack", "version": VERSION}, headers={"Access-Control-Allow-Origin": "*"})


@app.get("/api/status")
def api_status():
    ok, message = db.test_connection()
    return JSONResponse({"ok": ok, "message": message})


@app.get("/api/lots")
def api_lots(code: str = ""):
    rows = store.recent_lots(code) if code else []
    return [{"lot_no": r["lot_no"], "expire_date": r["expire_date"].isoformat() if r["expire_date"] else "",
             "manufacturer": r["manufacturer"] or ""} for r in rows]
