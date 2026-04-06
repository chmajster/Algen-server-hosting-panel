from panel import create_app
from panel.extensions import db
from panel.services.billing import run_billing_cycle


app = create_app("production")


with app.app_context():
    processed = run_billing_cycle()
    db.session.commit()
    print(f"Processed cycles: {processed}")
