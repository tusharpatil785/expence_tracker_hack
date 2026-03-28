from flask import Blueprint, render_template, jsonify
from flask_login import login_required, current_user
from models.models import Expense, ExpenseApproval, Notification, Budget, User
from utils.helpers import get_expense_analytics
from datetime import datetime, timedelta
from app import db

dashboard = Blueprint('dashboard', __name__)

@dashboard.route('/dashboard')
@login_required
def home():
    company_id = current_user.company_id
    role = current_user.role
    now = datetime.utcnow()

    # Analytics
    analytics = get_expense_analytics(company_id)

    # Role-based data
    if role in ('Admin', 'Finance', 'Director', 'CFO'):
        recent_expenses = Expense.query.filter_by(company_id=company_id)\
            .order_by(Expense.created_at.desc()).limit(10).all()
        pending_count = Expense.query.filter(
            Expense.company_id == company_id,
            Expense.status.in_(['Pending', 'In Review'])
        ).count()
    elif role == 'Manager':
        # Expenses from subordinates
        sub_ids = [u.id for u in current_user.subordinates]
        recent_expenses = Expense.query.filter(
            Expense.user_id.in_(sub_ids)
        ).order_by(Expense.created_at.desc()).limit(10).all()
        pending_approvals = ExpenseApproval.query.filter_by(
            approver_id=current_user.id, status='Pending'
        ).count()
        pending_count = pending_approvals
    else:
        recent_expenses = Expense.query.filter_by(user_id=current_user.id)\
            .order_by(Expense.created_at.desc()).limit(10).all()
        pending_count = Expense.query.filter(
            Expense.user_id == current_user.id,
            Expense.status.in_(['Pending', 'In Review'])
        ).count()

    # Notifications
    notifications = Notification.query.filter_by(
        user_id=current_user.id, is_read=False
    ).order_by(Notification.created_at.desc()).limit(5).all()

    # Budget overview
    budgets = Budget.query.filter_by(company_id=company_id, year=now.year).all()

    # My expenses this month
    my_month_expenses = Expense.query.filter(
        Expense.user_id == current_user.id,
        Expense.created_at >= datetime(now.year, now.month, 1)
    ).all()
    my_month_total = sum(e.converted_amount or 0 for e in my_month_expenses)

    return render_template('dashboard/home.html',
        analytics=analytics,
        recent_expenses=recent_expenses,
        pending_count=pending_count,
        notifications=notifications,
        budgets=budgets,
        my_month_total=my_month_total,
        now=now
    )

@dashboard.route('/dashboard/analytics')
@login_required
def analytics():
    data = get_expense_analytics(current_user.company_id)
    return render_template('dashboard/analytics.html', analytics=data)

@dashboard.route('/notifications/mark-read/<int:notif_id>', methods=['POST'])
@login_required
def mark_notification_read(notif_id):
    notif = Notification.query.filter_by(id=notif_id, user_id=current_user.id).first()
    if notif:
        notif.is_read = True
        db.session.commit()
    return jsonify({'success': True})

@dashboard.route('/notifications/mark-all-read', methods=['POST'])
@login_required
def mark_all_read():
    Notification.query.filter_by(user_id=current_user.id, is_read=False).update({'is_read': True})
    db.session.commit()
    return jsonify({'success': True})
