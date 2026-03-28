from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, current_app
from flask_login import login_required, current_user
from app import db
from models.models import Expense, ExpenseApproval, AuditLog
from utils.helpers import (convert_amount, get_all_currencies, select_flow_for_expense,
                            initialize_approval_chain, check_expense_policy,
                            extract_receipt_data, _notify_approver)
from datetime import datetime, date
import os, uuid

expenses = Blueprint('expenses', __name__)

CATEGORIES = [
    'Travel', 'Meals & Entertainment', 'Accommodation', 'Office Supplies',
    'Software & Subscriptions', 'Marketing', 'Training & Education',
    'Client Entertainment', 'Transportation', 'Medical', 'Equipment',
    'Communication', 'Miscellaneous'
]

@expenses.route('/expenses')
@login_required
def list_expenses():
    role = current_user.role
    page = request.args.get('page', 1, type=int)
    status_filter   = request.args.get('status', '')
    category_filter = request.args.get('category', '')
    date_from = request.args.get('date_from', '')
    date_to   = request.args.get('date_to', '')

    if role in ('Admin', 'Finance', 'Director', 'CFO'):
        query = Expense.query.filter_by(company_id=current_user.company_id)
    elif role == 'Manager':
        sub_ids = [u.id for u in current_user.subordinates] + [current_user.id]
        query = Expense.query.filter(Expense.user_id.in_(sub_ids))
    else:
        query = Expense.query.filter_by(user_id=current_user.id)

    if status_filter:
        query = query.filter(Expense.status == status_filter)
    if category_filter:
        query = query.filter(Expense.category == category_filter)
    if date_from:
        query = query.filter(Expense.date >= datetime.strptime(date_from, '%Y-%m-%d').date())
    if date_to:
        query = query.filter(Expense.date <= datetime.strptime(date_to, '%Y-%m-%d').date())

    expenses_list = query.order_by(Expense.created_at.desc()).paginate(page=page, per_page=15)
    return render_template('expenses/list.html',
        expenses=expenses_list,
        categories=CATEGORIES,
        status_filter=status_filter,
        category_filter=category_filter
    )

@expenses.route('/expenses/new', methods=['GET', 'POST'])
@login_required
def new_expense():
    currencies = get_all_currencies()
    company = current_user.company
    if request.method == 'POST':
        title       = request.form.get('title', '').strip()
        amount      = float(request.form.get('amount', 0))
        currency    = request.form.get('currency', company.base_currency)
        category    = request.form.get('category', '')
        subcategory = request.form.get('subcategory', '')
        description = request.form.get('description', '')
        exp_date    = request.form.get('date', str(date.today()))
        tags        = request.form.get('tags', '')
        project_code = request.form.get('project_code', '')
        cost_center  = request.form.get('cost_center', '')
        reimbursable = request.form.get('reimbursable') == 'on'

        if not title or amount <= 0 or not category:
            flash('Title, amount, and category are required.', 'error')
            return render_template('expenses/new.html', currencies=currencies, categories=CATEGORIES, company=company)

        # Currency conversion
        converted, rate = convert_amount(amount, currency, company.base_currency)

        # Handle receipt upload
        receipt_url = None
        receipt_ocr = None
        if 'receipt' in request.files:
            file = request.files['receipt']
            if file and file.filename:
                ext = os.path.splitext(file.filename)[1].lower()
                if ext in ('.jpg', '.jpeg', '.png', '.pdf', '.gif'):
                    fname = f"{uuid.uuid4().hex}{ext}"
                    fpath = os.path.join(current_app.config['UPLOAD_FOLDER'], fname)
                    os.makedirs(os.path.dirname(fpath), exist_ok=True)
                    file.save(fpath)
                    receipt_url = f"/static/uploads/{fname}"
                    # Try OCR
                    if ext in ('.jpg', '.jpeg', '.png'):
                        ocr_data = extract_receipt_data(fpath)
                        import json
                        receipt_ocr = json.dumps(ocr_data)
                else:
                    flash('Invalid file type. Use JPG, PNG, or PDF.', 'error')

        # Policy check
        policy_ok, policy_notes = check_expense_policy({
            'category': category, 'amount': converted, 'receipt': receipt_url
        }, current_user.company_id)

        expense = Expense(
            user_id=current_user.id,
            company_id=current_user.company_id,
            title=title,
            amount=amount,
            currency=currency,
            converted_amount=converted,
            exchange_rate=rate,
            category=category,
            subcategory=subcategory,
            description=description,
            date=datetime.strptime(exp_date, '%Y-%m-%d').date(),
            receipt_url=receipt_url,
            receipt_ocr_data=receipt_ocr,
            status='Pending',
            tags=tags,
            project_code=project_code,
            cost_center=cost_center,
            reimbursable=reimbursable,
            policy_compliant=policy_ok,
            policy_notes=policy_notes
        )
        db.session.add(expense)
        db.session.flush()

        # Select and initialize approval flow
        flow = select_flow_for_expense(expense, current_user.company_id)
        if flow:
            expense.flow_id = flow.id
            db.session.flush()
            initialize_approval_chain(expense, flow, db)
        else:
            expense.status = 'Approved'  # No flow = auto-approved
            db.session.commit()

        # Audit log
        log = AuditLog(
            company_id=current_user.company_id,
            user_id=current_user.id,
            action='Expense Submitted',
            entity_type='Expense',
            entity_id=expense.id,
            new_value=f'Amount: {amount} {currency} → {converted} {company.base_currency}'
        )
        db.session.add(log)
        db.session.commit()

        flash('Expense submitted successfully!', 'success')
        return redirect(url_for('expenses.view_expense', expense_id=expense.id))

    return render_template('expenses/new.html',
        currencies=currencies, categories=CATEGORIES, company=company,
        today=date.today().isoformat()
    )

@expenses.route('/expenses/<int:expense_id>')
@login_required
def view_expense(expense_id):
    expense = Expense.query.get_or_404(expense_id)
    # Access control
    if expense.company_id != current_user.company_id:
        flash('Access denied.', 'error')
        return redirect(url_for('expenses.list_expenses'))
    if current_user.role == 'Employee' and expense.user_id != current_user.id:
        flash('Access denied.', 'error')
        return redirect(url_for('expenses.list_expenses'))
    approvals = ExpenseApproval.query.filter_by(expense_id=expense_id)\
        .order_by(ExpenseApproval.step_number).all()
    return render_template('expenses/detail.html', expense=expense, approvals=approvals)

@expenses.route('/expenses/<int:expense_id>/cancel', methods=['POST'])
@login_required
def cancel_expense(expense_id):
    expense = Expense.query.get_or_404(expense_id)
    if expense.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    if expense.status not in ('Pending', 'In Review'):
        return jsonify({'error': 'Cannot cancel expense in current state'}), 400
    expense.status = 'Cancelled'
    db.session.commit()
    flash('Expense cancelled.', 'info')
    return redirect(url_for('expenses.list_expenses'))

@expenses.route('/expenses/<int:expense_id>/resubmit', methods=['POST'])
@login_required
def resubmit_expense(expense_id):
    expense = Expense.query.get_or_404(expense_id)
    if expense.user_id != current_user.id or expense.status != 'Rejected':
        flash('Cannot resubmit this expense.', 'error')
        return redirect(url_for('expenses.view_expense', expense_id=expense_id))
    # Reset approvals
    ExpenseApproval.query.filter_by(expense_id=expense_id).delete()
    expense.status = 'Pending'
    expense.current_step = 1
    db.session.flush()
    flow = expense.flow
    if flow:
        initialize_approval_chain(expense, flow, db)
    db.session.commit()
    flash('Expense resubmitted for approval.', 'success')
    return redirect(url_for('expenses.view_expense', expense_id=expense_id))

@expenses.route('/expenses/export')
@login_required
def export_expenses():
    """Export expenses as CSV."""
    import csv, io
    from flask import Response
    query = Expense.query.filter_by(company_id=current_user.company_id)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID','Title','Submitter','Amount','Currency','Converted','Base Currency',
                     'Category','Status','Date','Project Code','Cost Center','Created At'])
    for e in query.all():
        writer.writerow([
            e.id, e.title, e.submitter.name, e.amount, e.currency,
            e.converted_amount, e.company.base_currency,
            e.category, e.status, e.date, e.project_code, e.cost_center,
            e.created_at.strftime('%Y-%m-%d %H:%M')
        ])
    output.seek(0)
    return Response(output, mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=expenses.csv'})
