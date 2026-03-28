from app import db, login_manager
from flask_login import UserMixin
from datetime import datetime
import json

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

class Company(db.Model):
    __tablename__ = 'companies'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    base_currency = db.Column(db.String(10), default='USD')
    country = db.Column(db.String(100))
    industry = db.Column(db.String(100))
    logo_url = db.Column(db.String(300))
    expense_limit_employee = db.Column(db.Float, default=5000.0)
    expense_limit_manager = db.Column(db.Float, default=25000.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    users = db.relationship('User', backref='company', lazy=True)
    expenses = db.relationship('Expense', backref='company', lazy=True)
    approval_flows = db.relationship('ApprovalFlow', backref='company', lazy=True)
    budgets = db.relationship('Budget', backref='company', lazy=True)
    audit_logs = db.relationship('AuditLog', backref='company', lazy=True)

class User(db.Model, UserMixin):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), default='Employee')  # Admin, Manager, Finance, Director, CFO, Employee
    department = db.Column(db.String(100))
    designation = db.Column(db.String(100))
    employee_id = db.Column(db.String(50))
    phone = db.Column(db.String(20))
    avatar_url = db.Column(db.String(300))
    manager_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    last_login = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Relationships
    subordinates = db.relationship('User', backref=db.backref('manager', remote_side=[id]), lazy=True)
    expenses = db.relationship('Expense', foreign_keys='Expense.user_id', backref='submitter', lazy=True)
    approvals_given = db.relationship('ExpenseApproval', foreign_keys='ExpenseApproval.approver_id', backref='approver', lazy=True)
    notifications = db.relationship('Notification', backref='user', lazy=True)

class Expense(db.Model):
    __tablename__ = 'expenses'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(10), nullable=False)
    converted_amount = db.Column(db.Float)
    exchange_rate = db.Column(db.Float, default=1.0)
    category = db.Column(db.String(100))
    subcategory = db.Column(db.String(100))
    description = db.Column(db.Text)
    date = db.Column(db.Date, nullable=False)
    receipt_url = db.Column(db.String(300))
    receipt_ocr_data = db.Column(db.Text)  # JSON
    status = db.Column(db.String(50), default='Pending')  # Pending, In Review, Approved, Rejected, Cancelled
    current_step = db.Column(db.Integer, default=1)
    flow_id = db.Column(db.Integer, db.ForeignKey('approval_flows.id'), nullable=True)
    policy_compliant = db.Column(db.Boolean, default=True)
    policy_notes = db.Column(db.Text)
    tags = db.Column(db.String(300))  # comma separated
    project_code = db.Column(db.String(100))
    cost_center = db.Column(db.String(100))
    reimbursable = db.Column(db.Boolean, default=True)
    reimbursed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    # Relationships
    approvals = db.relationship('ExpenseApproval', backref='expense', lazy=True, cascade='all, delete-orphan')
    comments = db.relationship('ExpenseComment', backref='expense', lazy=True, cascade='all, delete-orphan')

class ApprovalFlow(db.Model):
    __tablename__ = 'approval_flows'
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text)
    is_default = db.Column(db.Boolean, default=False)
    min_amount = db.Column(db.Float, default=0)
    max_amount = db.Column(db.Float, nullable=True)
    applies_to_category = db.Column(db.String(200))  # JSON list or 'ALL'
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    steps = db.relationship('ApprovalStep', backref='flow', lazy=True, order_by='ApprovalStep.step_number', cascade='all, delete-orphan')
    rules = db.relationship('ApprovalRule', backref='flow', lazy=True, cascade='all, delete-orphan')
    expenses = db.relationship('Expense', backref='flow', lazy=True)

class ApprovalStep(db.Model):
    __tablename__ = 'approval_steps'
    id = db.Column(db.Integer, primary_key=True)
    flow_id = db.Column(db.Integer, db.ForeignKey('approval_flows.id'), nullable=False)
    step_number = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String(100))
    role = db.Column(db.String(50))  # Manager, Finance, Director, CFO, Specific User
    specific_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    is_parallel = db.Column(db.Boolean, default=False)  # parallel or sequential
    timeout_hours = db.Column(db.Integer, default=48)
    auto_approve_on_timeout = db.Column(db.Boolean, default=False)
    specific_user = db.relationship('User', foreign_keys=[specific_user_id])

class ExpenseApproval(db.Model):
    __tablename__ = 'expense_approvals'
    id = db.Column(db.Integer, primary_key=True)
    expense_id = db.Column(db.Integer, db.ForeignKey('expenses.id'), nullable=False)
    step_number = db.Column(db.Integer, nullable=False)
    approver_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    status = db.Column(db.String(50), default='Pending')  # Pending, Approved, Rejected, Delegated
    comment = db.Column(db.Text)
    delegated_to = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    responded_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class ApprovalRule(db.Model):
    __tablename__ = 'approval_rules'
    id = db.Column(db.Integer, primary_key=True)
    flow_id = db.Column(db.Integer, db.ForeignKey('approval_flows.id'), nullable=False)
    step_number = db.Column(db.Integer)  # null = applies to all steps
    rule_type = db.Column(db.String(50))  # percentage, special_role, hybrid, unanimous
    percentage = db.Column(db.Float, default=100.0)
    special_role = db.Column(db.String(50))
    rule_config = db.Column(db.Text)  # JSON for complex rules

class Budget(db.Model):
    __tablename__ = 'budgets'
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=False)
    department = db.Column(db.String(100))
    category = db.Column(db.String(100))
    amount = db.Column(db.Float, nullable=False)
    spent = db.Column(db.Float, default=0.0)
    period = db.Column(db.String(20))  # monthly, quarterly, annual
    year = db.Column(db.Integer)
    month = db.Column(db.Integer, nullable=True)
    quarter = db.Column(db.Integer, nullable=True)
    currency = db.Column(db.String(10), default='USD')
    alert_threshold = db.Column(db.Float, default=80.0)  # % at which to alert
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class ExpensePolicy(db.Model):
    __tablename__ = 'expense_policies'
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=False)
    category = db.Column(db.String(100))
    max_amount_per_claim = db.Column(db.Float)
    max_amount_per_day = db.Column(db.Float)
    requires_receipt_above = db.Column(db.Float, default=25.0)
    allowed_currencies = db.Column(db.String(300))  # JSON
    description = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)

class ExpenseComment(db.Model):
    __tablename__ = 'expense_comments'
    id = db.Column(db.Integer, primary_key=True)
    expense_id = db.Column(db.Integer, db.ForeignKey('expenses.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    comment = db.Column(db.Text, nullable=False)
    is_internal = db.Column(db.Boolean, default=False)  # internal = only visible to approvers
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User')

class Notification(db.Model):
    __tablename__ = 'notifications'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    title = db.Column(db.String(200))
    message = db.Column(db.Text)
    type = db.Column(db.String(50))  # approval_request, expense_update, budget_alert, system
    related_expense_id = db.Column(db.Integer, db.ForeignKey('expenses.id'), nullable=True)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class AuditLog(db.Model):
    __tablename__ = 'audit_logs'
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    action = db.Column(db.String(200))
    entity_type = db.Column(db.String(100))
    entity_id = db.Column(db.Integer)
    old_value = db.Column(db.Text)
    new_value = db.Column(db.Text)
    ip_address = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User')

class ReportTemplate(db.Model):
    __tablename__ = 'report_templates'
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=False)
    name = db.Column(db.String(150))
    report_type = db.Column(db.String(100))
    config = db.Column(db.Text)  # JSON
    schedule = db.Column(db.String(50))  # daily, weekly, monthly
    recipients = db.Column(db.Text)  # JSON list of emails
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
