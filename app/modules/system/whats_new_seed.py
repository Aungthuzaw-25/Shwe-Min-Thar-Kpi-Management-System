"""Launch seed entries for the what's-new page (Database design
§7 — LIVE-ON-MAIN ONLY).

Plain Burmese copy; Zenn corrects it from Frontend previews.
Topics 1–10 are features live on main 92ac2a8; the migrated
rows are the static whats_new.njk entries translated to
Burmese (the 25 Oct "Formula period links" entry FOLDS into
topic 6 — it is not duplicated). NOT seeded: anything
unmerged at 92ac2a8 (period-overlap block, per-device
session revoke, System Health graphs, alembic widen fix,
D3 toggle) — seeding them would advertise unshipped
features.

Seeding mechanism (NOT migration data): run once per
environment at deploy — ``python scripts/seed_whats_new.py``.
Idempotent by title: a row with the same title is never
re-inserted, so re-running cannot duplicate rows, and a
seed an SA deliberately soft-deleted is never resurrected.
Topic published_at stamps are staggered 1 minute apart at
seed time (display order = list order); migrated rows keep
their original static-list dates.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.future import select

from app.core.timezone import APPLICATION_TIMEZONE
from app.models.system import WhatsNewEntry

# link_path values below are routes verified in
# web/src/routes/dashboard.js (gates in parentheses):
# /tasks (employee) · /superadmin/employee-kpi-results
# (Super Admin) · /superadmin/kpi (Super Admin, Admin) ·
# /kpi-periods (Super Admin, Admin) · /superadmin/reports
# (Super Admin, Admin) · /superadmin/system-logs
# (Super Admin) · /system-settings (Super Admin).
# Theme-wide / everywhere features have NO link (null) —
# a wrong link is worse than no link.
SEED_ENTRIES = [
    {
        "title": "အခန်းကဏ္ဍအလိုက် အရောင်အသစ်",
        "body": "စာမျက်နှာတွေကို အသုံးပြုသူရဲ့ အခန်းကဏ္ဍအလိုက် အရောင်ခွဲထားပါပြီ — Super Admin က အပြာရင့်၊ Admin/HR က အညိုနီ၊ ဝန်ထမ်းက အဝါ၊ လိမ္မော်၊ အနီ ဖြစ်ပါတယ်။",
        "how_to": None,
        "link_path": None,
        "link_label": None,
        "visible_to": "ALL",
        "status": "PUBLISHED",
    },
    {
        "title": "ဘေးဘား မီနူးခွဲများ",
        "body": "ဘေးဘားက မီနူးတွေကို အဖွဲ့လိုက် ခွဲထားပါပြီ — ဥပမာ Users ကို နှိပ်ရင် Active Users နဲ့ Deleted Users ဆိုပြီး ခွဲမြင်ရပါတယ်။",
        "how_to": None,
        "link_path": None,
        "link_label": None,
        "visible_to": "ALL",
        "status": "PUBLISHED",
    },
    {
        "title": "ဒေါင်းလုဒ်ခလုတ်နဲ့ ဖိုင်တင်တဲ့ ပုံစံအသစ်",
        "body": "ဒေါင်းလုဒ်ခလုတ်တွေက ဖန်သားမှန်ပုံစံ ဖြစ်သွားပြီး၊ ဖိုင်တင်တဲ့နေရာမှာ ဖိုင်ကို ဆွဲချတင်နိုင်တဲ့ နေရာ ပါလာပါပြီ။ လုပ်ဆောင်ချက်နဲ့ ခွင့်ပြုချက်တွေက အရင်အတိုင်းပါပဲ။",
        "how_to": None,
        "link_path": None,
        "link_label": None,
        "visible_to": "ALL",
        "status": "PUBLISHED",
    },
    {
        "title": "လုပ်ငန်းစဉ် စာမျက်နှာက ခလုတ်တန်း အသစ်",
        "body": "လုပ်ငန်းစဉ် စာမျက်နှာက အပိုင်းခွဲတွေကို အလုံးပုံစံ ခလုတ်တန်းနဲ့ ရွေးနိုင်ပါပြီ — အကြောင်းအရာနဲ့ လုပ်ဆောင်ချက်က အရင်အတိုင်းပါပဲ။",
        "how_to": None,
        "link_path": "/tasks",
        "link_label": None,
        "visible_to": "EMPLOYEE",
        "status": "PUBLISHED",
    },
    {
        "title": "ဝန်ထမ်း KPI ရလဒ် စာမျက်နှာ သီးသန့်",
        "body": "ဝန်ထမ်း KPI ရလဒ်တွေကို ဒက်ရှ်ဘုတ်ထဲကနေ ခွဲထုတ်ပြီး သီးသန့် စာမျက်နှာ ဖြစ်သွားပါပြီ — ဘေးဘားက Dashboard အောက်က Employee KPI results ကနေ ဖွင့်နိုင်ပါတယ်။",
        "how_to": None,
        "link_path": "/superadmin/employee-kpi-results",
        "link_label": None,
        "visible_to": "SUPER_ADMIN",
        "status": "PUBLISHED",
    },
    {
        "title": "ဖော်မြူလာအတွက် ကာလ ရွေးချယ်မှု",
        "body": "ဖော်မြူလာတစ်ခု ဘယ်ကာလတွေမှာ အကျုံးဝင်လဲ ဆိုတာကို ရွေးနိုင်ပါပြီ — ကာလ ရွေးတဲ့နေရာကနေ ရွေးတာ၊ ဒါမှမဟုတ် လအလိုက် အပိုင်းအခြား ပေးတာ လုပ်နိုင်ပြီး၊ မရှိသေးတဲ့ လအတွက် ကာလကို အလိုအလျောက် ဖန်တီးပေးပါတယ်။",
        "how_to": "ဖော်မြူလာ ပြင်တဲ့နေရာမှာ ကာလ ရွေးပါ — တစ်ခုတည်း ရွေးတာ၊ ဒါမှမဟုတ် လအပိုင်းအခြား ပေးတာပါ။",
        "link_path": "/superadmin/kpi",
        "link_label": None,
        "visible_to": "ADMIN",
        "status": "PUBLISHED",
    },
    {
        "title": "KPI ကာလ စာမျက်နှာ အသစ်",
        "body": "KPI ကာလတွေကို စာမျက်နှာအသစ်ကနေ ကြည့်နိုင်ပြီး၊ ကာလ ဖန်တီးတာ၊ အခြေအနေ ပြောင်းတာတွေကို ပိုရှင်းတဲ့ ပုံစံနဲ့ လုပ်နိုင်ပါပြီ။",
        "how_to": None,
        "link_path": "/kpi-periods",
        "link_label": None,
        "visible_to": "ADMIN",
        "status": "PUBLISHED",
    },
    {
        "title": "သတ်မှတ်ထားတာထက် ကျော်လွန်တဲ့ ရလဒ် အမှတ်အသား",
        "body": "KPI ရလဒ်က သတ်မှတ်ထားတဲ့ ပစ်မှတ်ထက် ကျော်လွန်ရင် အမှတ်အသားနဲ့ အသိပေးချက် ပြပါပြီ — အမှတ်ကတော့ အမြင့်ဆုံးထက် မကျော်ပါဘူး။",
        "how_to": None,
        "link_path": None,
        "link_label": None,
        "visible_to": "ALL",
        "status": "PUBLISHED",
    },
    {
        "title": "အမှားစာတွေ အတိအကျ ပြခြင်း",
        "body": "တစ်ခုခု မှားရင် 'ကွန်ရက် အမှား' လို့ အတိုပြောမယ့်အစား၊ ဆာဗာက ပေးတဲ့ အကြောင်းရင်း အတိအကျကို ပြပါပြီ။",
        "how_to": None,
        "link_path": None,
        "link_label": None,
        "visible_to": "ALL",
        "status": "PUBLISHED",
    },
    {
        "title": "ထွက်ခွာသွားတဲ့ ဝန်ထမ်းမှတ်တမ်း Reports မှာ",
        "body": "ထွက်ခွာသွားတဲ့ ဝန်ထမ်းတွေရဲ့ မှတ်တမ်းကို Reports စာမျက်နှာမှာ ရှာနိုင်ပါပြီ — Users စာမျက်နှာမှာတော့ လက်ရှိနဲ့ ဖျက်ထားတဲ့ အသုံးပြုသူတွေပဲ ရှိပါတယ်။",
        "how_to": None,
        "link_path": "/superadmin/reports",
        "link_label": None,
        "visible_to": "ADMIN",
        "status": "PUBLISHED",
    },
    # Migrated static entries (original static-list dates kept).
    {
        "title": "ဖောင်ဖြည့်လက်စ အလိုအလျောက် သိမ်းခြင်း",
        "body": "ဖောင်တစ်ဝက် ဖြည့်ပြီးမှ စာမျက်နှာက ထွက်သွားရင်လည်း ဖြည့်ထားတာ မပျောက်တော့ပါဘူး — ပြန်လာဖြည့်နိုင်ပြီး၊ ထပ်မံတင်သွင်းလို့ ထပ်နေတဲ့ မှတ်တမ်း မဖြစ်တော့ပါဘူး။",
        "how_to": None,
        "link_path": None,
        "link_label": None,
        "visible_to": "ALL",
        "status": "PUBLISHED",
        "published_at": datetime(2026, 10, 24, tzinfo=APPLICATION_TIMEZONE),
    },
    {
        "title": "KPI ကာလကို နှစ်အလိုက် ရှာခြင်း",
        "body": "KPI ကာလတွေကို နှစ်အလိုက် စစ်ထုတ်ရှာနိုင်ပါပြီ — တစ်နှစ်လုံးရဲ့ ကာလတွေကို တစ်ခါတည်း မြင်ရပါတယ်။",
        "how_to": "KPI ကာလ စာမျက်နှာမှာ နှစ်ကို ရွေးပြီး စစ်ပါ။",
        "link_path": "/kpi-periods",
        "link_label": None,
        "visible_to": "ADMIN",
        "status": "PUBLISHED",
        "published_at": datetime(2026, 10, 24, tzinfo=APPLICATION_TIMEZONE),
    },
    {
        "title": "စနစ် မှတ်တမ်းတွေ ရိုးရိုးစာနဲ့ ဖတ်နိုင်ခြင်း",
        "body": "စနစ် မှတ်တမ်းတိုင်းကို ရိုးရိုးစာနဲ့ ဖတ်နိုင်ပြီး၊ စာမျက်နှာခွဲနဲ့ လှန်ကြည့်နိုင်ပါပြီ။",
        "how_to": None,
        "link_path": "/superadmin/system-logs",
        "link_label": None,
        "visible_to": "SUPER_ADMIN",
        "status": "PUBLISHED",
        "published_at": datetime(2026, 10, 24, tzinfo=APPLICATION_TIMEZONE),
    },
    {
        "title": "အော့ဒစ် မှတ်တမ်း သိမ်းဆည်းမှု ဆက်တင်",
        "body": "အော့ဒစ် မှတ်တမ်းတွေကို ထာဝရ သိမ်းမလား၊ ၁ နှစ်၊ ၂ နှစ်၊ ဒါမှမဟုတ် ၅ နှစ် ပြည့်ရင် အလိုအလျောက် ဖျက်မလား ဆိုတာ ရွေးနိုင်ပါပြီ။",
        "how_to": None,
        "link_path": "/system-settings",
        "link_label": None,
        "visible_to": "SUPER_ADMIN",
        "status": "PUBLISHED",
        "published_at": datetime(2026, 10, 24, tzinfo=APPLICATION_TIMEZONE),
    },
    {
        "title": "အင်တာနက် မရှိလည်း ဒက်ရှ်ဘုတ် ကြည့်နိုင်ခြင်း (ဖတ်ရန်သာ)",
        "body": "အင်တာနက် ပြတ်သွားရင်လည်း ဒက်ရှ်ဘုတ်တွေကို ဖတ်နိုင်ပါပြီ — အော့ဖ်လိုင်း ဖြစ်နေကြောင်း နဖူးစည်းမှာ ပြပါတယ်။ ပြင်ဆင်တာတွေကတော့ အင်တာနက် ပြန်ရမှ လုပ်နိုင်ပါတယ်။",
        "how_to": None,
        "link_path": None,
        "link_label": None,
        "visible_to": "ALL",
        "status": "PUBLISHED",
        "published_at": datetime(2026, 10, 23, tzinfo=APPLICATION_TIMEZONE),
    },
]


async def seed_whats_new(db) -> tuple[int, int]:
    """Insert missing seed entries; return (inserted, skipped).

    Topic rows (no explicit published_at) are stamped at seed
    time, staggered 1 minute apart in list order so the newest
    topic lists first. Migrated rows keep their fixed dates.
    """
    now = datetime.now(UTC)
    inserted = 0
    skipped = 0
    for index, data in enumerate(SEED_ENTRIES):
        existing = (
            await db.execute(
                select(WhatsNewEntry.id).where(
                    WhatsNewEntry.title == data["title"]
                )
            )
        ).scalars().first()
        if existing is not None:
            skipped += 1
            continue
        published_at = data.get("published_at")
        if published_at is None:
            published_at = now - timedelta(minutes=index)
        db.add(
            WhatsNewEntry(
                title=data["title"],
                body=data["body"],
                how_to=data["how_to"],
                link_path=data["link_path"],
                link_label=data["link_label"],
                image_path=None,
                visible_to=data["visible_to"],
                status=data["status"],
                published_at=published_at,
                is_deleted=False,
            )
        )
        inserted += 1
    await db.commit()
    return inserted, skipped
