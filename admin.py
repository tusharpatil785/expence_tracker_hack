from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from functools import wraps
from app import db, bcrypt
from models.models import (User, Company, ApprovalFlow, ApprovalStep, ApprovalRule,
                            Budget, ExpensePolicy, AuditLog)
import json

admin = Blueprint('admin', __name__)

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'Admin':
            flash('Admin access required.', 'error')
            return redirect(url_for('dashboard.home'))
        return f(*args, **kwargs)
    return decorated

# ─── Users ───────────────────────────────────────────────────────────────────

@admin.route('/admin/users')
@login_required
@admin_required
def users():
    all_users = User.query.filter_by(company_id=current_user.company_id).all()
    return render_template('admin/users.html', users=all_users)

@admin.route('/admin/users/new', methods=['GET', 'POST'])
@login_required
@admin_required
def new_user():
    managers = User.query.filter(
        User.company_id == current_user.company_id,
        User.role.in_(['Manager', 'Admin', 'Director', 'CFO', 'Finance'])
    ).all()
    if request.method == 'POST':
        name       = request.form.get('name', '').strip()
        email      = request.form.get('email', '').strip().lower()
        role       = request.form.get('role', 'Employee')
        manager_id = request.form.get('manager_id', type=int)
        department = request.form.get('department', '')
        designation = request.form.get('designation', '')
        employee_id = request.form.get('employee_id', '')
        phone      = request.form.get('phone', '')
        password   = request.form.get('password', 'Xpense@123')

        if User.query.filter_by(email=email).first():
            flash('Email already exists.', 'error')
            return render_template('admin/new_user.html', managers=managers)

        hashed = bcrypt.generate_password_hash(password).decode('utf-8')
        user = User(
            name=name, email=email, password=hashed,
            role=role, manager_id=manager_id,
            department=department, designation=designation,
            employee_id=employee_id, phone=phone,
            company_id=current_user.company_id
        )
        db.session.add(user)
        log = AuditLog(company_id=current_user.company_id, user_id=current_user.id,
                       action='User Created', entity_type='User', new_value=f'{name} ({email}) as {role}')
        db.session.add(log)
        db.session.commit()
        flash(f'User {name} created successfully!', 'success')
        return redirect(url_for('admin.users'))
    return render_template('admin/new_user.html', managers=managers)

@admin.route('/admin/users/<int:user_id>/toggle', methods=['POST'])
@login_required
@admin_required
def toggle_user(user_id):
    user = User.query.filter_by(id=user_id, company_id=current_user.company_id).first_or_404()
    if user.id == current_user.id:
        return jsonify({'error': 'Cannot deactivate yourself'}), 400
    user.is_active = not user.is_active
    db.session.commit()
    return jsonify({'active': user.is_active})

@admin.route('/admin/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_user(user_id):
    user = User.query.filter_by(id=user_id, company_id=current_user.company_id).first_or_404()
    managers = User.query.filter(
        User.company_id == current_user.company_id,
        User.id != user_id,
        User.role.in_(['Manager', 'Admin', 'Director', 'CFO', 'Finance'])
    ).all()
    if request.method == 'POST':
        user.name        = request.form.get('name', user.name)
        user.role        = request.form.get('role', user.role)
        user.department  = request.form.get('department', user.department)
        user.designation = request.form.get('designation', user.designation)
        user.manager_id  = request.form.get('manager_id', type=int)
        db.session.commit()
        flash('User updated.', 'success')
        return redirect(url_for('admin.users'))
    return render_template('admin/edit_user.html', user=user, managers=managers)

# ─── Approval Flows ──────────────────────────────────────────────────────────

@admin.route('/admin/flows')
@login_required
@admin_required
def flows():
    all_flows = ApprovalFlow.query.filter_by(company_id=current_user.company_id).all()
    return render_template('admin/flows.html', flows=all_flows)

@admin.route('/admin/flows/new', methods=['GET', 'POST'])
@login_required
@admin_required
def new_flow():
    from routes.expenses import CATEGORIES
    if request.method == 'POST':
        name        = request.form.get('name', '').strip()
        description = request.form.get('description', '')
        is_default  = request.form.get('is_default') == 'on'
        min_amount  = float(request.form.get('min_amount', 0) or 0)
        max_amount  = request.form.get('max_amount') or None
        if max_amount:
            max_amount = float(max_amount)
        categories  = request.form.getlist('categories')
        cat_val     = 'ALL' if not categories or 'ALL' in categories else json.dumps(categories)

        # Unset other defaults
        if is_default:
            ApprovalFlow.query.filter_by(company_id=current_user.company_id, is_default=True)\
                .update({'is_default': False})

        flow = ApprovalFlow(
            company_id=current_user.company_id,
            name=name, description=description,
            is_default=is_default,
            min_amount=min_amount, max_amount=max_amount,
            applies_to_category=cat_val,
            is_active=True
        )
        db.session.add(flow)
        db.session.flush()

        # Steps
        step_nums = request.form.getlist('step_number')
        step_roles = request.form.getlist('step_role')
        step_names = request.form.getlist('step_name')
        step_timeouts = request.form.getlist('step_timeout')
        for i, snum in enumerate(step_nums):
            if not step_roles[i]:
                continue
            step = ApprovalStep(
                flow_id=flow.id,
                step_number=int(snum),
                name=step_names[i] if i < len(step_names) else f'Step {snum}',
                role=step_roles[i],
                timeout_hours=int(step_timeouts[i]) if i < len(step_timeouts) and step_timeouts[i] else 48
            )
            db.session.add(step)

        # Rule
        rule_type   = request.form.get('rule_type', 'unanimous')
        percentage  = float(request.form.get('rule_percentage', 100) or 100)
        special_role = request.form.get('special_role', '')
        rule = ApprovalRule(
            flow_id=flow.id,
            rule_type=rule_type,
            percentage=percentage,
            special_role=special_role
        )
        db.session.add(rule)
        db.session.commit()
        flash(f'Approval flow "{name}" created!', 'success')
        return redirect(url_for('admin.flows'))
    return render_template('admin/new_flow.html', categories=CATEGORIES)

@admin.route('/admin/flows/<int:flow_id>/toggle', methods=['POST'])
@login_required
@admin_required
def toggle_flow(flow_id):
    flow = ApprovalFlow.query.filter_by(id=flow_id, company_id=current_user.company_id).first_or_404()
    flow.is_active = not flow.is_active
    db.session.commit()
    return jsonify({'active': flow.is_active})

# ─── Budgets ─────────────────────────────────────────────────────────────────

@admin.route('/admin/budgets')
@login_required
@admin_required
def budgets():
    from datetime import datetime
    all_budgets = Budget.query.filter_by(company_id=current_user.company_id)\
        .filter_by(year=datetime.utcnow().year).all()
    return render_template('admin/budgets.html', budgets=all_budgets)

@admin.route('/admin/budgets/new', methods=['GET', 'POST'])
@login_required
@admin_required
def new_budget():
    from routes.expenses import CATEGORIES
    from datetime import datetime
    if request.method == 'POST':
        budget = Budget(
            company_id=current_user.company_id,
            department=request.form.get('department', ''),
            category=request.form.get('category', ''),
            amount=float(request.form.get('amount', 0)),
            period=request.form.get('period', 'annual'),
            year=int(request.form.get('year', datetime.utcnow().year)),
            currency=current_user.company.base_currency,
            alert_threshold=float(request.form.get('alert_threshold', 80))
        )
        db.session.add(budget)
        db.session.commit()
        flash('Budget created!', 'success')
        return redirect(url_for('admin.budgets'))
    return render_template('admin/new_budget.html', categories=CATEGORIES,
                           current_year=datetime.utcnow().year)

# ─── Policies ────────────────────────────────────────────────────────────────

@admin.route('/admin/policies')
@login_required
@admin_required
def policies():
    all_policies = ExpensePolicy.query.filter_by(company_id=current_user.company_id).all()
    return render_template('admin/policies.html', policies=all_policies)

@admin.route('/admin/policies/new', methods=['GET', 'POST'])
@login_required
@admin_required
def new_policy():
    from routes.expenses import CATEGORIES
    if request.method == 'POST':
        policy = ExpensePolicy(
            company_id=current_user.company_id,
            category=request.form.get('category', ''),
            max_amount_per_claim=float(request.form.get('max_per_claim') or 0) or None,
            max_amount_per_day=float(request.form.get('max_per_day') or 0) or None,
            requires_receipt_above=float(request.form.get('receipt_above') or 25),
            description=request.form.get('description', ''),
            is_active=True
        )
        db.session.add(policy)
        db.session.commit()
        flash('Policy created!', 'success')
        return redirect(url_for('admin.policies'))
    return render_template('admin/new_policy.html', categories=CATEGORIES)

# ─── Audit Logs ──────────────────────────────────────────────────────────────

@admin.route('/admin/audit')
@login_required
@admin_required
def audit_logs():
    page = request.args.get('page', 1, type=int)
    logs = AuditLog.query.filter_by(company_id=current_user.company_id)\
        .order_by(AuditLog.created_at.desc()).paginate(page=page, per_page=30)
    return render_template('admin/audit.html', logs=logs)

# ─── Company Settings ─────────────────────────────────────────────────────────

@admin.route('/admin/settings', methods=['GET', 'POST'])
@login_required
@admin_required
def settings():
    from utils.helpers import get_all_currencies
    company = current_user.company
    currencies = get_all_currencies()
    if request.method == 'POST':
        company.name = request.form.get('name', company.name)
        company.base_currency = request.form.get('base_currency', company.base_currency)
        company.industry = request.form.get('industry', company.industry)
        company.expense_limit_employee = float(request.form.get('limit_employee', 5000) or 5000)
        company.expense_limit_manager  = float(request.form.get('limit_manager', 25000) or 25000)
        db.session.commit()
        flash('Company settings updated.', 'success')
        return redirect(url_for('admin.settings'))
    return render_template('admin/settings.html', company=company, currencies=currencies)
