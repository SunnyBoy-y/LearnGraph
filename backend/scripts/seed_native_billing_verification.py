"""Seed only an explicitly named, isolated browser-verification database."""
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

if "native-billing-implementation/live-test.db" not in os.environ.get("LEARNGRAPH_DATABASE_URL", "").replace("\\", "/"):
    raise SystemExit("Only the isolated billing verification database is allowed")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal
from app.domain.models import ProviderConfig, UsageEvent, User
from app.services.billing import BillingService

payload = json.load(sys.stdin)
with SessionLocal() as db:
    if payload["phase"] == "identities":
        owner = db.get(User, payload["owner"])
        owner.is_system_admin = True
        for user_id in payload["members"]:
            db.get(User, user_id).tenant_id = owner.tenant_id
        db.commit()
    else:
        for workspace, prefix in payload["workspaces"]:
            provider_id = str(uuid4())
            db.add(ProviderConfig(id=provider_id, workspace_id=workspace, display_name="Fixture provider " + prefix,
                provider_type="openai_compatible_chat", base_url="http://127.0.0.1:24139/fixture-provider",
                enabled=False, remote_capability=True, status="configured_disabled", capabilities={"default_model": "native-fixture"}))
            db.commit()
            billing = BillingService(db, workspace, payload["owner"])
            for currency, value, count in (("USD", "0.4100000000001", 3), ("CNY", "1.5000000000001", 2)):
                if prefix == "personal":
                    value = "2.2200000000001" if currency == "USD" else "3.2100000000001"
                price = billing.create_price(provider_id=provider_id, model_id="native-fixture", currency=currency,
                    input_per_million="0.1", cached_input_per_million="0", cache_write_per_million="0.3", output_per_million="0.2")
                for _ in range(count):
                    db.add(UsageEvent(workspace_id=workspace, provider_id=provider_id, model_id="native-fixture", feature="chat",
                        currency=currency, cost_amount=value, amount_source="provider_reported", cost_status="provider_reported",
                        price_version_id=price.id, input_tokens=1000, cached_input_tokens=100, output_tokens=500, total_tokens=1500,
                        pricing_snapshot={"schema_version": 2, "currency": currency, "fixture": True}))
                db.add(UsageEvent(workspace_id=workspace, provider_id=provider_id, model_id="native-fixture", feature="vision",
                    currency=currency, cost_amount=None, reserved_amount="0.01", amount_source="unknown", cost_status="usage_missing",
                    pricing_snapshot={"schema_version": 2, "currency": currency, "fixture": True}))
                db.commit()
                billing.create_budget_policy(name=currency + " fixture cap", currency=currency, hard_limit="10", soft_limit="8")
            db.add(UsageEvent(workspace_id=workspace, provider_id=provider_id, model_id="legacy", feature="chat", currency=None,
                cost_amount=None, amount_source="legacy_unknown", cost_status="legacy_unknown", legacy_billing_snapshot={"cost_usd": 9, "cost_cny": 60}))
            db.commit()
print(json.dumps({"ok": True}))
