import os
import sys

# Ensure the script's directory is on sys.path so local packages (e.g. `utils`, `models`)
# can be imported when running the file directly.
BASE_DIR = os.path.dirname(__file__)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user # type: ignore

# Helpers and models used by the API endpoints
# Imports are done inside functions to avoid import-time side effects and
# to keep static analyzers from complaining when running outside the app package.

api = Blueprint('api', __name__)

@api.route('/exchange-rate')
@login_required
def exchange_rate():
    from utils.helpers import get_exchange_rate
    from_cur = request.args.get('from', 'USD')
    # guard company/base_currency in case the user has no company set
    to_cur   = request.args.get('to', getattr(getattr(current_user, 'company', None), 'base_currency', 'USD'))
    rate = get_exchange_rate(from_cur, to_cur)
    return jsonify({'rate': rate, 'from': from_cur, 'to': to_cur})

@api.route('/country-currency/<country_code>')
def country_currency(country_code):
    from utils.helpers import get_country_currency
    data = get_country_currency(country_code)
    return jsonify(data)

@api.route('/notifications')
@login_required
def notifications():
    from models.models import Notification
    notifs = Notification.query.filter_by(
        user_id=current_user.id, is_read=False
    ).order_by(Notification.created_at.desc()).limit(10).all()
    return jsonify([{
        'id': n.id,
        'title': n.title,
        'message': n.message,
        'type': n.type,
        'created_at': n.created_at.strftime('%Y-%m-%d %H:%M'),
        'expense_id': n.related_expense_id
    } for n in notifs])

@api.route('/analytics')
@login_required
def analytics():
    from utils.helpers import get_expense_analytics
    data = get_expense_analytics(current_user.company_id)
    return jsonify(data)

@api.route('/users/search')
@login_required
def search_users():
    q = request.args.get('q', '')
    from models.models import User
    users = User.query.filter(
        User.company_id == current_user.company_id,
        User.name.ilike(f'%{q}%'),
        User.is_active == True
    ).limit(10).all()
    return jsonify([{'id': u.id, 'name': u.name, 'role': u.role, 'email': u.email} for u in users])

@api.route('/expenses/stats')
@login_required
def expense_stats():
    from datetime import datetime
    from models.models import Expense
    now = datetime.utcnow()
    my_expenses = Expense.query.filter_by(user_id=current_user.id)
    return jsonify({
        'total_submitted': my_expenses.count(),
        'approved': my_expenses.filter_by(status='Approved').count(),
        'pending': my_expenses.filter(Expense.status.in_(['Pending', 'In Review'])).count(),
        'rejected': my_expenses.filter_by(status='Rejected').count(),
        'total_amount': sum(e.converted_amount or 0 for e in my_expenses.filter_by(status='Approved').all())
    })
