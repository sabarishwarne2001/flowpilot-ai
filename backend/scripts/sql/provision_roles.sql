-- ARCH-07 §B.3 / §B.7. Role provisioning for database immutability & sweeper separation.
-- Run once as PostgreSQL superuser during deployment.

CREATE ROLE servabo_app      LOGIN PASSWORD 'app_password';
CREATE ROLE servabo_sweeper  LOGIN PASSWORD 'sweeper_password';

GRANT CONNECT ON DATABASE servabo TO servabo_app, servabo_sweeper;
GRANT USAGE   ON SCHEMA public      TO servabo_app, servabo_sweeper;

-- Application role: full DML everywhere EXCEPT audit_logs, where it is append-only.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO servabo_app;
REVOKE UPDATE, DELETE, TRUNCATE ON audit_logs FROM servabo_app;
GRANT  SELECT, INSERT              ON audit_logs TO servabo_app;

-- Sweeper role: narrow. DELETE on exactly the tables it sweeps.
GRANT SELECT, DELETE ON audit_logs            TO servabo_sweeper;
GRANT SELECT, DELETE ON uploaded_files        TO servabo_sweeper;
GRANT SELECT, UPDATE ON email_change_requests TO servabo_sweeper;

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO servabo_app;
