# SMTP Encryption Key Rotation Runbook (ARCH-07 Step 9)

## Preconditions

1. Full database backup, verified restorable.
2. `--verify` on current key set returns 0 pending, 0 failed.

## Step 1: Prepend New Key

Generate new Fernet key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Update `.env`:

```env
EMAIL_ENCRYPTION_KEYS=<NEW_KEY>,<OLD_KEY>
```

## Step 2: Dry Run

```bash
python scripts/reencrypt_smtp_passwords.py --dry-run
```

## Step 3: Apply Re-Encryption

```bash
python scripts/reencrypt_smtp_passwords.py --apply --i-have-a-verified-backup
python scripts/reencrypt_smtp_passwords.py --verify
```

## Step 4: Drop Old Key

Update `.env` to keep only the new head key:

```env
EMAIL_ENCRYPTION_KEYS=<NEW_KEY>
```