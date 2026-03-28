from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from app import db
from models.models import Expense, ExpenseApproval, ExpenseComment, AuditLog
from utils.helpers import process_approval_decision

approvals = Blueprint('approvals', __name__)

APPROVER_ROLES = ('Manager', 'Finance', 'Director', 'CFO', 'Admin')

@approvals.route('/approvals')
@login_required
def list_approvals():
    if current_user.role not in APPROVER_ROLES:
        flash('You do not have approval permissions.', 'error')
        return redirect(url_for('dashboard.home'))
    page = request.args.get('page', 1, type=int)
    status_filter = request.args.get('status', 'Pending')

    pending = ExpenseApproval.query.filter_by(
        approver_id=current_user.id, status='Pending'
    ).all()
    pending_expense_ids = [a.expense_id for a in pending]

    if status_filter == 'Pending':
        exps = Expense.query.filter(
            Expense.id.in_(pending_expense_ids)
        ).order_by(Expense.created_at.asc()).paginate(page=page, per_page=15)
    else:
        # History of approvals given
        historical = ExpenseApproval.query.filter(
            ExpenseApproval.approver_id == current_user.id,
            ExpenseApproval.status != 'Pending'
        ).all()
        hist_ids = [a.expense_id for a in historical]
        exps = Expense.query.filter(
            Expense.id.in_(hist_ids)
        ).order_by(Expense.updated_at.desc()).paginate(page=page, per_page=15)

    return render_template('approvals/list.html',
        expenses=exps,
        pending_count=len(pending_expense_ids),
        status_filter=status_filter
    )

@approvals.route('/approvals/<int:expense_id>', methods=['GET', 'POST'])
@login_required
def review_expense(expense_id):
    if current_user.role not in APPROVER_ROLES:
        flash('Access denied.', 'error')
        return redirect(url_for('dashboard.home'))

    expense = Expense.query.get_or_404(expense_id)
    if expense.company_id != current_user.company_id:
        flash('Access denied.', 'error')
        return redirect(url_for('approvals.list_approvals'))

    # Check if user has a pending approval for this expense
    my_approval = ExpenseApproval.query.filter_by(
        expense_id=expense_id,
        approver_id=current_user.id,
        status='Pending'
    ).first()

    if request.method == 'POST':
        if not my_approval:
            flash('No pending approval found for you.', 'error')
            return redirect(url_for('approvals.list_approvals'))

        decision = request.form.get('decision')
        comment  = request.form.get('comment', '')

        if decision not in ('Approved', 'Rejected'):
            flash('Invalid decision.', 'error')
            return redirect(url_for('approvals.review_expense', expense_id=expense_id))

        success, msg = process_approval_decision(
            expense, expense.current_step, current_user, decision, comment, db
        )

        # Audit log
        log = AuditLog(
            company_id=current_user.company_id,
            user_id=current_user.id,
            action=f'Expense {decision}',
            entity_type='Expense',
            entity_id=expense_id,
            new_value=f'Step {expense.current_step}: {decision}. Comment: {comment}'
        )
        db.session.add(log)
        db.session.commit()

        flash(f'Expense {decision.lower()} successfully. {msg}', 'success')
        return redirect(url_for('approvals.list_approvals'))

    all_approvals = ExpenseApproval.query.filter_by(expense_id=expense_id)\
        .order_by(ExpenseApproval.step_number, ExpenseApproval.created_at).all()
    comments = ExpenseComment.query.filter_by(expense_id=expense_id)\
        .order_by(ExpenseComment.created_at).all()

    return render_template('approvals/review.html',
        expense=expense,
        my_approval=my_approval,
        all_approvals=all_approvals,
        comments=comments
    )

@approvals.route('/approvals/<int:expense_id>/comment', methods=['POST'])
@login_required
def add_comment(expense_id):
    expense = Expense.query.get_or_404(expense_id)
    comment_text = request.form.get('comment', '').strip()
    is_internal  = request.form.get('is_internal') == 'on'
    if comment_text:
        comment = ExpenseComment(
            expense_id=expense_id,
            user_id=current_user.id,
            comment=comment_text,
            is_internal=is_internal
        )
        db.session.add(comment)
        db.session.commit()
        flash('Comment added.', 'success')
    return redirect(url_for('approvals.review_expense', expense_id=expense_id))

@approvals.route('/approvals/<int:expense_id>/delegate', methods=['POST'])
@login_required
def delegate_approval(expense_id):
    my_approval = ExpenseApproval.query.filter_by(
        expense_id=expense_id, approver_id=current_user.id, status='Pending'
    ).first()
    if not my_approval:
        return jsonify({'error': 'No pending approval found'}), 400
    delegate_to = request.form.get('delegate_to', type=int)
    from models.models import User
    delegatee = User.query.filter_by(id=delegate_to, company_id=current_user.company_id).first()
    if not delegatee:
        flash('Invalid user for delegation.', 'error')
        return redirect(url_for('approvals.review_expense', expense_id=expense_id))
    my_approval.status    = 'Delegated'
    my_approval.delegated_to = delegate_to
    # Create new approval for delegate
    new_approval = ExpenseApproval(
        expense_id=expense_id,
        step_number=my_approval.step_number,
        approver_id=delegate_to,
        status='Pending'
    )
    db.session.add(new_approval)
    from utils.helpers import _notify_approver
    expense = Expense.query.get(expense_id)
    _notify_approver(expense, delegatee, db)
    db.session.commit()
    flash(f'Approval delegated to {delegatee.name}.', 'success')
    return redirect(url_for('approvals.list_approvals'))

@approvals.route('/approvals/bulk', methods=['POST'])
@login_required
def bulk_approve():
    """Bulk approve multiple expenses at once."""
    expense_ids = request.form.getlist('expense_ids')
    decision    = request.form.get('decision', 'Approved')
    comment     = request.form.get('comment', 'Bulk action')
    count = 0
    for eid in expense_ids:
        expense = Expense.query.get(int(eid))
        if not expense:
            continue
        my_approval = ExpenseApproval.query.filter_by(
            expense_id=int(eid), approver_id=current_user.id, status='Pending'
        ).first()
        if my_approval:
            process_approval_decision(expense, expense.current_step, current_user, decision, comment, db)
            count += 1
    flash(f'{count} expenses {decision.lower()} successfully.', 'success')
    return redirect(url_for('approvals.list_approvals'))
