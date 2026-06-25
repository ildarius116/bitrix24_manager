"""Read-only проба СПА «Рабочий день» (1208) и «Учёт рабочего времени» (1218).
Пишет чистый UTF-8 в out/probe.txt."""
import os, sys, json, pathlib, io
from dotenv import load_dotenv

load_dotenv(pathlib.Path(__file__).parent / ".env")
sys.path.insert(0, str(pathlib.Path(__file__).parent / ".claude" / "skills" / "bitrix24-agent" / "scripts"))
from bitrix24_client import Bitrix24Client, TenantConfig, BitrixAPIError  # type: ignore

client = Bitrix24Client(TenantConfig(
    domain=os.environ["B24_DOMAIN"], auth_mode="webhook",
    webhook_user_id=os.environ["B24_WEBHOOK_USER_ID"], webhook_code=os.environ["B24_WEBHOOK_CODE"],
), max_attempts=2)

out = io.StringIO()
def w(s=""): out.write(s + "\n")

def fields(tid):
    w(f"\n========== FIELDS entityTypeId={tid} ==========")
    try:
        f = client.call("crm.item.fields", {"entityTypeId": tid})
        for k, v in (f.get("result") or {}).get("fields", {}).items():
            v = v or {}
            w(f"    {k:34} {v.get('type',''):12} {v.get('title','')}")
    except BitrixAPIError as e:
        w(f"    ОШИБКА {e.code}: {e}")

def get_item(tid, iid):
    w(f"\n========== ITEM entityTypeId={tid} id={iid} ==========")
    try:
        r = client.call("crm.item.get", {"entityTypeId": tid, "id": iid})
        item = (r.get("result") or {}).get("item", {})
        for k, v in item.items():
            w(f"    {k:34} = {v!r}")
    except BitrixAPIError as e:
        w(f"    ОШИБКА {e.code}: {e}")

def list_top(tid, n=5):
    w(f"\n========== TOP {n} entityTypeId={tid} (по id desc) ==========")
    try:
        r = client.call("crm.item.list", {"entityTypeId": tid, "order": {"id": "desc"}, "start": 0})
        items = (r.get("result") or {}).get("items", [])[:n]
        for it in items:
            w(f"    id={it.get('id')}  title={it.get('title')!r}")
    except BitrixAPIError as e:
        w(f"    ОШИБКА {e.code}: {e}")

fields(1208); fields(1218)
get_item(1208, 269698); get_item(1208, 270608)
get_item(1218, 162468); get_item(1218, 163025)
list_top(1208); list_top(1218)

pathlib.Path("out").mkdir(exist_ok=True)
pathlib.Path("out/probe.txt").write_text(out.getvalue(), encoding="utf-8")
print("written out/probe.txt")
