import requests
import json
import os
from datetime import datetime, timedelta
from flask import current_app
try:
    import pytesseract
    from PIL import Image
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

# ─── Currency Utilities ──────────────────────────────────────────────────────

CURRENCY_API = "https://api.exchangerate-api.com/v4/latest/{}"
COUNTRY_API  = "https://restcountries.com/v3.1/alpha/{}"

# Fallback rates if API is unavailable
FALLBACK_RATES = {
    "USD": 1.0, "EUR": 0.92, "GBP": 0.79, "INR": 83.5,
    "JPY": 149.5, "CAD": 1.36, "AUD": 1.53, "CHF": 0.89,
    "CNY": 7.24, "SGD": 1.34, "AED": 3.67, "SAR": 3.75,
    "MXN": 17.2, "BRL": 4.97, "ZAR": 18.6, "KRW": 1325.0,
    "HKD": 7.82, "NOK": 10.6, "SEK": 10.4, "DKK": 6.89
}

def get_exchange_rate(from_currency: str, to_currency: str) -> float:
    """Get real-time exchange rate between two currencies."""
    if from_currency == to_currency:
        return 1.0
    try:
        resp = requests.get(CURRENCY_API.format(from_currency), timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            rates = data.get('rates', {})
            return rates.get(to_currency, 1.0)
    except Exception:
        pass
    # Fallback: convert via USD
    from_usd = FALLBACK_RATES.get(from_currency, 1.0)
    to_usd   = FALLBACK_RATES.get(to_currency, 1.0)
    return to_usd / from_usd

def convert_amount(amount: float, from_currency: str, to_currency: str):
    """Convert amount and return (converted_amount, exchange_rate)."""
    rate = get_exchange_rate(from_currency, to_currency)
    return round(amount * rate, 2), rate

def get_country_currency(country_code: str) -> dict:
    """Fetch currency info for a country using restcountries API."""
    try:
        resp = requests.get(COUNTRY_API.format(country_code), timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if data:
                country = data[0]
                currencies = country.get('currencies', {})
                if currencies:
                    code = list(currencies.keys())[0]
                    name = currencies[code].get('name', code)
                    symbol = currencies[code].get('symbol', code)
                    return {'code': code, 'name': name, 'symbol': symbol,
                            'country': country.get('name', {}).get('common', country_code)}
    except Exception:
        pass
    return {'code': 'USD', 'name': 'US Dollar', 'symbol': '$', 'country': country_code}

def get_all_currencies():
    """Return common currencies for dropdown."""
    return [
        ('USD','US Dollar'), ('EUR','Euro'), ('GBP','British Pound'),
        ('INR','Indian Rupee'), ('JPY','Japanese Yen'), ('CAD','Canadian Dollar'),
        ('AUD','Australian Dollar'), ('CHF','Swiss Franc'), ('CNY','Chinese Yuan'),
        ('SGD','Singapore Dollar'), ('AED','UAE Dirham'), ('SAR','Saudi Riyal'),
        ('MXN','Mexican Peso'), ('BRL','Brazilian Real'), ('ZAR','South African Rand'),
        ('KRW','South Korean Won'), ('HKD','Hong Kong Dollar'), ('NOK','Norwegian Krone'),
        ('SEK','Swedish Krona'), ('DKK','Danish Krone')
    ]

# ─── Workflow Engine ─────────────────────────────────────────────────────────

def select_flow_for_expense(expense, company_id):
    """Intelligently pick the best approval flow for an expense."""
    from models.models import ApprovalFlow
    flows = ApprovalFlow.query.filter_by(
        company_id=company_id, is_active=True
    ).order_by(ApprovalFlow.min_amount.desc()).all()

    for flow in flows:
        # Category match
        if flow.applies_to_category and flow.applies_to_category != 'ALL':
            cats = json.loads(flow.applies_to_category)
            if expense.category not in cats:
                continue
        # Amount range match
        if flow.min_amount is not None and expense.converted_amount < flow.min_amount:
            continue
        if flow.max_amount is not None and expense.converted_amount > flow.max_amount:
            continue
        return flow

    # Fallback: default flow
    return ApprovalFlow.query.filter_by(
        company_id=company_id, is_default=True, is_active=True
    ).first()

def initialize_approval_chain(expense, flow, db):
    """Create pending ExpenseApproval records for step 1 of the flow."""
    from models.models import ApprovalStep, ExpenseApproval, User
    if not flow:
        return
    step = ApprovalStep.query.filter_by(
        flow_id=flow.id, step_number=1
    ).first()
    if not step:
        return
    # Find approver(s)
    approvers = resolve_approvers(step, expense.submitter)
    for approver in approvers:
        record = ExpenseApproval(
            expense_id=expense.id,
            step_number=1,
            approver_id=approver.id,
            status='Pending'
        )
        db.session.add(record)
    expense.current_step = 1
    expense.status = 'In Review'
    db.session.commit()

def resolve_approvers(step, submitter):
    """Find all users who should approve a given step."""
    from models.models import User
    if step.specific_user_id:
        user = User.query.get(step.specific_user_id)
        return [user] if user else []
    if step.role == 'Manager':
        if submitter.manager:
            return [submitter.manager]
        return []
    # Role-based
    company_id = submitter.company_id
    users = User.query.filter_by(
        company_id=company_id, role=step.role, is_active=True
    ).all()
    return users

def process_approval_decision(expense, step_number, approver, decision, comment, db):
    """Handle an approval or rejection, apply rules, and advance workflow."""
    from models.models import ExpenseApproval, ApprovalStep, ApprovalRule, Notification
    # Record the decision
    approval = ExpenseApproval.query.filter_by(
        expense_id=expense.id,
        step_number=step_number,
        approver_id=approver.id,
        status='Pending'
    ).first()
    if not approval:
        return False, 'No pending approval found'

    approval.status = decision
    approval.comment = comment
    approval.responded_at = datetime.utcnow()
    db.session.commit()

    if decision == 'Rejected':
        expense.status = 'Rejected'
        db.session.commit()
        _notify_submitter(expense, 'rejected', db)
        return True, 'Expense rejected'

    # Check conditional rules for this step
    flow = expense.flow
    rule = ApprovalRule.query.filter_by(
        flow_id=flow.id, step_number=step_number
    ).first() if flow else None
    if not rule:
        rule = ApprovalRule.query.filter_by(
            flow_id=flow.id, step_number=None
        ).first() if flow else None

    all_approvals = ExpenseApproval.query.filter_by(
        expense_id=expense.id, step_number=step_number
    ).all()
    total = len(all_approvals)
    approved_count = sum(1 for a in all_approvals if a.status == 'Approved')

    step_passed = _evaluate_rule(rule, all_approvals, approved_count, total, approver)

    if not step_passed:
        return True, 'Decision recorded, waiting for other approvers'

    # Advance to next step
    next_step_num = step_number + 1
    next_step = ApprovalStep.query.filter_by(
        flow_id=flow.id, step_number=next_step_num
    ).first() if flow else None

    if next_step:
        approvers = resolve_approvers(next_step, expense.submitter)
        for ap in approvers:
            record = ExpenseApproval(
                expense_id=expense.id,
                step_number=next_step_num,
                approver_id=ap.id,
                status='Pending'
            )
            db.session.add(record)
            _notify_approver(expense, ap, db)
        expense.current_step = next_step_num
        db.session.commit()
        return True, f'Moved to step {next_step_num}'
    else:
        # Final approval
        expense.status = 'Approved'
        db.session.commit()
        _notify_submitter(expense, 'approved', db)
        _check_budget_update(expense, db)
        return True, 'Expense fully approved'

def _evaluate_rule(rule, approvals, approved_count, total, current_approver):
    """Returns True if the step's approval condition is met."""
    if not rule:
        # Default: all must approve
        pending = sum(1 for a in approvals if a.status == 'Pending')
        return pending == 0 and approved_count == total

    rtype = rule.rule_type
    if rtype == 'unanimous':
        pending = sum(1 for a in approvals if a.status == 'Pending')
        return pending == 0 and approved_count == total
    elif rtype == 'percentage':
        pct = (approved_count / total * 100) if total > 0 else 0
        return pct >= rule.percentage
    elif rtype == 'special_role':
        if current_approver.role == rule.special_role:
            return True
        # Also allow if special role already approved
        for a in approvals:
            if a.approver and a.approver.role == rule.special_role and a.status == 'Approved':
                return True
        return False
    elif rtype == 'hybrid':
        # Percentage OR special role
        pct = (approved_count / total * 100) if total > 0 else 0
        if pct >= (rule.percentage or 60):
            return True
        if current_approver.role == rule.special_role:
            return True
        return False
    return approved_count > 0

def _notify_submitter(expense, action, db):
    from models.models import Notification
    msg_map = {
        'approved': ('✅ Expense Approved', f'Your expense "{expense.title}" has been approved.'),
        'rejected': ('❌ Expense Rejected', f'Your expense "{expense.title}" was rejected.'),
    }
    title, message = msg_map.get(action, ('Update', 'Your expense was updated.'))
    notif = Notification(
        user_id=expense.user_id,
        title=title,
        message=message,
        type='expense_update',
        related_expense_id=expense.id
    )
    db.session.add(notif)
    db.session.commit()

def _notify_approver(expense, approver, db):
    from models.models import Notification
    notif = Notification(
        user_id=approver.id,
        title='🔔 Approval Required',
        message=f'Expense "{expense.title}" by {expense.submitter.name} needs your review.',
        type='approval_request',
        related_expense_id=expense.id
    )
    db.session.add(notif)
    db.session.commit()

def _check_budget_update(expense, db):
    from models.models import Budget, Notification
    now = datetime.utcnow()
    budget = Budget.query.filter_by(
        company_id=expense.company_id,
        category=expense.category,
        year=now.year
    ).first()
    if budget:
        budget.spent = (budget.spent or 0) + (expense.converted_amount or 0)
        pct = (budget.spent / budget.amount * 100) if budget.amount else 0
        if pct >= budget.alert_threshold:
            # Notify admins
            from models.models import User
            admins = User.query.filter_by(company_id=expense.company_id, role='Admin').all()
            for admin in admins:
                n = Notification(
                    user_id=admin.id,
                    title='⚠️ Budget Alert',
                    message=f'{expense.category} budget is at {pct:.1f}% utilization.',
                    type='budget_alert'
                )
                db.session.add(n)
        db.session.commit()

# ─── Policy Engine ───────────────────────────────────────────────────────────

def check_expense_policy(expense, company_id):
    """Check expense against company policies. Returns (compliant, notes)."""
    from models.models import ExpensePolicy
    policy = ExpensePolicy.query.filter_by(
        company_id=company_id,
        category=expense.get('category'),
        is_active=True
    ).first()
    notes = []
    compliant = True
    if policy:
        amount = float(expense.get('amount', 0))
        if policy.max_amount_per_claim and amount > policy.max_amount_per_claim:
            notes.append(f'Exceeds max per claim: {policy.max_amount_per_claim}')
            compliant = False
        if policy.requires_receipt_above and amount > policy.requires_receipt_above:
            if not expense.get('receipt'):
                notes.append(f'Receipt required for amounts over {policy.requires_receipt_above}')
    return compliant, '; '.join(notes)

# ─── OCR Module ──────────────────────────────────────────────────────────────

def extract_receipt_data(image_path: str) -> dict:
    """Use Tesseract OCR to extract data from a receipt image."""
    if not OCR_AVAILABLE:
        return {'error': 'OCR not available. Install pytesseract and Pillow.'}
    try:
        img = Image.open(image_path)
        text = pytesseract.image_to_string(img)
        return parse_receipt_text(text)
    except Exception as e:
        return {'error': str(e), 'raw_text': ''}

def parse_receipt_text(text: str) -> dict:
    """Simple rule-based receipt parser."""
    import re
    lines = text.strip().split('\n')
    result = {'raw_text': text, 'merchant': '', 'amount': None, 'date': None, 'items': []}
    # Merchant: usually first non-empty line
    for line in lines:
        if line.strip():
            result['merchant'] = line.strip()
            break
    # Amount: look for total pattern
    total_patterns = [
        r'(?i)(total|amount|grand total)[:\s]*\$?([\d,]+\.?\d*)',
        r'\$\s*([\d,]+\.\d{2})',
        r'([\d,]+\.\d{2})\s*(?:USD|EUR|GBP|INR)?'
    ]
    for pattern in total_patterns:
        match = re.search(pattern, text)
        if match:
            amount_str = match.group(2) if len(match.groups()) > 1 else match.group(1)
            try:
                result['amount'] = float(amount_str.replace(',', ''))
                break
            except ValueError:
                pass
    # Date
    date_patterns = [
        r'\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b',
        r'\b(\d{4}[/-]\d{2}[/-]\d{2})\b',
        r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{1,2},?\s+\d{4}\b'
    ]
    for pattern in date_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            result['date'] = match.group(0)
            break
    return result

# ─── Analytics Helpers ───────────────────────────────────────────────────────

def get_expense_analytics(company_id, period='monthly'):
    """Return analytics data for dashboards."""
    from models.models import Expense, User
    from sqlalchemy import func
    now = datetime.utcnow()
    query = Expense.query.filter_by(company_id=company_id)

    # Category breakdown
    cat_data = {}
    for exp in query.filter(Expense.status == 'Approved').all():
        cat_data[exp.category] = cat_data.get(exp.category, 0) + (exp.converted_amount or 0)

    # Monthly trend (last 6 months)
    monthly = []
    for i in range(5, -1, -1):
        month = (now.month - i - 1) % 12 + 1
        year  = now.year - ((now.month - i - 1) // 12)
        total = sum(
            e.converted_amount or 0
            for e in query.filter(
                func.strftime('%Y', Expense.date) == str(year),
                func.strftime('%m', Expense.date) == f'{month:02d}'
            ).all()
        )
        monthly.append({'month': f'{year}-{month:02d}', 'total': total})

    # Status breakdown
    status_data = {}
    for exp in query.all():
        status_data[exp.status] = status_data.get(exp.status, 0) + 1

    return {
        'category_breakdown': cat_data,
        'monthly_trend': monthly,
        'status_breakdown': status_data,
        'total_approved': sum(e.converted_amount or 0 for e in query.filter_by(status='Approved').all()),
        'total_pending': sum(e.converted_amount or 0 for e in query.filter_by(status='Pending').all()),
        'count_pending': query.filter_by(status='In Review').count() + query.filter_by(status='Pending').count(),
    }
