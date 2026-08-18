# Secure Ticket Management System - Technical Documentation

## 1. Overview

### What the app does
This Flask web application supports submitting, triaging, tracking, and analyzing secure ticket requests.

Core capabilities:
- Authentication with Flask-Login (login, logout, registration)
- Role-based access control (stakeholder, internal, admin)
- Request lifecycle management (create, edit, view, delete with role guards)
- Review workflow (mark reviewed/unreviewed)
- Priority scoring model with rationale capture
- Duplicate title detection for project submissions
- Dashboard filtering, sorting, and role-aware visibility (My vs All)
- Calendar and Insights pages
- CSV export aligned with dashboard filters
- Admin user management (create, edit role, delete user, change password)

### Purpose
ATLAS standardizes intake data and improves prioritization transparency by:
- Enforcing consistent request metadata
- Applying a shared scoring framework with required rationale fields
- Restricting privileged actions by role
- Providing operational visibility through dashboard, calendar, and insights

## 2. Recent Changes (July 2026)

### Branding update
- Product branding is now the Secure Ticket Management System.
- Header/navbar title now displays the secure ticket experience in a neutral, reusable format.
- A global footer appears on every page with a generic secure ticket tagline.
- Base page title is updated to the secure ticket experience.

### Admin password management
- Added admin-only route: `/change_password/<int:user_id>`.
- Validations:
  - New password is required
  - Minimum length is 8 characters
  - New password and confirmation must match
- Uses `User.query.get_or_404(user_id)` for safe lookup.
- Passwords are saved as hashes only via `generate_password_hash`.
- Success flow flashes `Password updated successfully.` and redirects to `/users`.
- User management actions now include Edit, Change Password, Delete.

### Browser autocomplete hardening
Autocomplete is disabled on sensitive/user-admin forms to reduce browser autofill issues:
- Login
- Register
- User Management (create user)
- Edit User
- Change Password

## 3. Architecture

### Stack
- Backend: Flask
- ORM/DB: Flask-SQLAlchemy + SQLite
- Auth/session: Flask-Login
- Templates/UI: Jinja2 + Bootstrap + targeted vanilla JavaScript helpers

### High-level flow
1. User authenticates and accesses a route.
2. Flask route enforces permissions and validates request data.
3. SQLAlchemy reads/writes Request and User records.
4. Jinja templates render role-aware UI.
5. Mutations are persisted and surfaced with flash messaging.

## 4. Data Model

### Request model
Key fields:
- `id` (PK)
- `request_number` (unique, integer)
- `project_name`
- `assigned_lead`
- `requested_by`
- `stakeholder_group`
- `content_type` (optional)
- `status` (New, In Progress, On Hold, Deferred, Completed)
- `description` (optional)
- `business_outcome` (required)
- `reviewed` (boolean)
- `created_at`
- `start_date`
- `end_date`
- `visibility` (legacy compatibility field; default 0)
- `business_value`, `reach`, `reuse`, `risk_compliance`, `feasibility` (0-5 each)
- `business_value_rationale`, `reach_rationale`, `reuse_rationale`, `risk_compliance_rationale`, `feasibility_rationale`
- `total_score`
- `priority` (Critical, High, Average, Low)

### User model
Key fields:
- `id` (PK)
- `username` (unique)
- `password` (hashed)
- `role` (stakeholder, internal, admin)

Methods:
- `set_password(raw_password)`
- `check_password(raw_password)`
- `set_role(role)` (normalized fallback to stakeholder)

## 5. Security and Access Control

### Authentication
- Flask-Login controls session state.
- `@login_required` protects application routes.
- Unauthenticated users are redirected to `/login`.

### Roles
Supported roles:
- stakeholder
- internal
- admin

### Request permissions
- stakeholder:
  - My view shows requests where `requested_by == current_user.username`
  - Cannot assign project lead
  - Cannot choose Requested By (forced to self)
  - Cannot edit feasibility directly
  - Can edit/delete only own unreviewed requests
- internal:
  - Can assign project lead
  - Can choose Requested By
  - Can edit feasibility
  - Can mark/unmark reviewed
  - Cannot delete requests
- admin:
  - Internal capabilities plus request deletion

### User management permissions
- `/users`, `/edit_user/<int:user_id>`, `/delete_user/<int:user_id>`, and `/change_password/<int:user_id>` are admin-only.
- Non-admin users are redirected to dashboard with error flash messages.

### Password handling
- Passwords are never displayed in plaintext.
- Password checks use `check_password_hash`.
- Password writes use `generate_password_hash`.
- Admin password change path updates hashes only.

## 6. Priority Model and Validation

### Scoring dimensions
Five dimensions are scored from 0 to 5:
- Business Value
- Reach
- Reuse
- Risk / Compliance
- Feasibility

Formula:
- `total_score = business_value + reach + reuse + risk_compliance + feasibility`

Score range:
- 0 to 25

Priority thresholds (`_calc_priority`):
- Critical: `>= 22`
- High: `>= 17`
- Average: `>= 11`
- Low: `< 11`

### Validation guards
- `_parse_score` clamps values to 0-5.
- `_parse_date` validates `YYYY-MM-DD`.
- Missing rationale fields are rejected.
- Business outcome is required.
- `requested_by` must be a valid username when selectable.
- Duplicate title detection blocks save when a similar project name is found.

### Review/status consistency
- Reviewed requests are forced to `In Progress`.
- Unreview sets status back to `New` when applicable.
- Status values are normalized to supported options.

## 7. Route Summary

### Auth routes
- `GET/POST /login`
- `GET/POST /register`
- `GET /logout`

### User admin routes (admin-only)
- `GET/POST /users`
- `GET/POST /edit_user/<int:user_id>`
- `POST /delete_user/<int:user_id>`
- `GET/POST /change_password/<int:user_id>`

### App routes (login required)
- `GET /dashboard`
- `GET /calendar`
- `GET /insights`
- `GET /settings`
- `GET /guide`
- `GET/POST /new`
- `GET/POST /edit/<int:request_id>`
- `GET /view/<int:request_id>`
- `POST /review/<int:id>`
- `POST /unreview/<int:id>`
- `POST /delete/<int:id>`
- `GET /download`

## 8. UI and Branding Notes

### Shared layout behavior
- Most pages extend `templates/base.html`, which now centralizes:
  - Secure ticket header content
  - Global footer branding and tagline
  - Flash message rendering
  - Shared theme and date-format helper scripts

### Form UX details
- Sensitive/auth-related forms set `autocomplete="off"`.
- Request form supports create/edit modes with role-aware field locking.
- User management page exposes action buttons for Edit, Change Password, Delete.

## 9. Startup Initialization and Lightweight Migration

On startup inside application context:
- `db.create_all()` ensures tables exist.
- Adds missing `request` columns when needed:
  - `start_date`, `visibility`, rationale fields, `business_outcome`, `reviewed`
- Backfills null/empty legacy values for compatibility.
- Recomputes and syncs `total_score` and `priority` from stored dimension values.
- Normalizes request statuses and reviewed/status alignment.
- For `user` table:
  - Adds `password` column if missing
  - Copies legacy `password_hash` into `password` if available
  - Adds/normalizes `role` values
  - Replaces null password with empty string

Note:
- This is a pragmatic SQLite-oriented migration approach.
- For production environments, migrate to versioned migrations (Alembic/Flask-Migrate).

## 10. Dependencies

Current requirements (`requirements.txt`):
- Flask==3.0.3
- Flask-SQLAlchemy==3.1.1
- Flask-Login==0.6.3
- pandas

## 11. Current Limitations and Next Steps

Current limitations:
- SQLite limits high-concurrency workloads.
- Startup migration strategy is not versioned.
- Some dashboard/insight aggregations are still in-memory.

Recommended next steps:
1. Introduce Flask-Migrate (Alembic) for schema versioning.
2. Move to PostgreSQL for multi-user scale.
3. Add automated tests for RBAC, auth, scoring, and CSV parity.
4. Add pagination for large result sets.
5. Add audit metadata (`created_by`, `updated_by`, `updated_at`).
