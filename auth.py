from flask import Blueprint, render_template, redirect, url_for, flash, request, session
from flask_login import login_user, logout_user, login_required, current_user
from app import db, bcrypt
from models.models import User, Company
from utils.helpers import get_country_currency, get_all_currencies
from datetime import datetime
import re

auth = Blueprint('auth', __name__)

def is_valid_email(email):
    return re.match(r'^[^@]+@[^@]+\.[^@]+$', email)

@auth.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.home'))
    return redirect(url_for('auth.login'))

@auth.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.home'))
    if request.method == 'POST':
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        remember = request.form.get('remember') == 'on'
        user = User.query.filter_by(email=email).first()
        if user and bcrypt.check_password_hash(user.password, password):
            if not user.is_active:
                flash('Your account has been deactivated. Contact admin.', 'error')
                return redirect(url_for('auth.login'))
            login_user(user, remember=remember)
            user.last_login = datetime.utcnow()
            db.session.commit()
            next_page = request.args.get('next')
            return redirect(next_page or url_for('dashboard.home'))
        flash('Invalid email or password.', 'error')
    return render_template('auth/login.html')

@auth.route('/signup', methods=['GET', 'POST'])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.home'))
    currencies = get_all_currencies()
    if request.method == 'POST':
        name         = request.form.get('name', '').strip()
        email        = request.form.get('email', '').strip().lower()
        password     = request.form.get('password', '')
        confirm_pass = request.form.get('confirm_password', '')
        company_name = request.form.get('company_name', '').strip()
        country      = request.form.get('country', 'US')
        currency     = request.form.get('currency', 'USD')
        industry     = request.form.get('industry', '')

        # Validation
        if not all([name, email, password, company_name]):
            flash('All fields are required.', 'error')
            return render_template('auth/signup.html', currencies=currencies)
        if not is_valid_email(email):
            flash('Invalid email address.', 'error')
            return render_template('auth/signup.html', currencies=currencies)
        if len(password) < 8:
            flash('Password must be at least 8 characters.', 'error')
            return render_template('auth/signup.html', currencies=currencies)
        if password != confirm_pass:
            flash('Passwords do not match.', 'error')
            return render_template('auth/signup.html', currencies=currencies)
        if User.query.filter_by(email=email).first():
            flash('Email already registered.', 'error')
            return render_template('auth/signup.html', currencies=currencies)

        # Create company
        company = Company(
            name=company_name,
            base_currency=currency,
            country=country,
            industry=industry
        )
        db.session.add(company)
        db.session.flush()

        # Create first user as Admin
        hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
        user = User(
            name=name,
            email=email,
            password=hashed_pw,
            role='Admin',
            company_id=company.id
        )
        db.session.add(user)
        db.session.flush()

        # Create default approval flow
        from models.models import ApprovalFlow, ApprovalStep, ApprovalRule
        default_flow = ApprovalFlow(
            company_id=company.id,
            name='Default Approval Flow',
            description='Standard single-manager approval',
            is_default=True,
            min_amount=0,
            max_amount=None,
            applies_to_category='ALL',
            is_active=True
        )
        db.session.add(default_flow)
        db.session.flush()

        step1 = ApprovalStep(
            flow_id=default_flow.id,
            step_number=1,
            name='Manager Approval',
            role='Manager',
            timeout_hours=48
        )
        db.session.add(step1)

        rule = ApprovalRule(
            flow_id=default_flow.id,
            rule_type='unanimous',
            percentage=100
        )
        db.session.add(rule)
        db.session.commit()

        login_user(user)
        flash(f'Welcome to Xpense, {name}! Company "{company_name}" created.', 'success')
        return redirect(url_for('dashboard.home'))

    return render_template('auth/signup.html', currencies=currencies)

@auth.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))

@auth.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        current_user.name        = request.form.get('name', current_user.name)
        current_user.phone       = request.form.get('phone', current_user.phone)
        current_user.department  = request.form.get('department', current_user.department)
        current_user.designation = request.form.get('designation', current_user.designation)
        new_password = request.form.get('new_password', '')
        if new_password:
            if len(new_password) < 8:
                flash('New password must be 8+ characters.', 'error')
                return redirect(url_for('auth.profile'))
            current_user.password = bcrypt.generate_password_hash(new_password).decode('utf-8')
        db.session.commit()
        flash('Profile updated successfully.', 'success')
    return render_template('auth/profile.html')
