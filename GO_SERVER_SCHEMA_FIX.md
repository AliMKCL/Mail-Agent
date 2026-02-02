# Go Server Schema Fix - Summary

## Problem
When syncing emails, the Go server returned:
```
Go server error: 500 - Failed to get credentials
Unexpected error calling Go server: 500: Error fetching emails from Go service: Failed to get credentials
```

## Root Cause
The Go server code was using the **old database schema** with table/column names that no longer exist after the Python database schema was updated.

## Schema Changes (Python → Go)

### Table Name Changes
| Old (Go was using) | New (Python uses) | Status |
|-------------------|-------------------|--------|
| `user_tokens` | `email_tokens` | ✅ Fixed |

### Column Name Changes
| Table | Old Column | New Column | Status |
|-------|-----------|------------|--------|
| `email_tokens` | `user_id` | `email_account_id` | ✅ Fixed |
| `emails` | `user_id` | `email_account_id` | ✅ Fixed |

### JSON Field Changes
| Old Field | New Field | Status |
|-----------|-----------|--------|
| `user_id` | `email_account_id` | ✅ Fixed |

## Files Modified

### 1. `backend/go-server/main.go`
**Changes:**
- Updated `messageIDs` struct field from `UserID` to `EmailAccountID`
- Updated JSON tag from `json:"user_id"` to `json:"email_account_id"`
- Updated all references to use `ids.EmailAccountID` instead of `ids.UserID`
- Updated `fetchWorker` function parameter from `userID` to `emailAccountID`

**Lines changed:** 36, 75, 90, 119, 150

### 2. `backend/go-server/auth.go`
**Changes:**
- Updated `getCredentials` function parameter from `userID` to `emailAccountID`
- Changed SQL query table name from `user_tokens` to `email_tokens`
- Changed SQL query column from `user_id` to `email_account_id`
- Updated error messages to use "email account ID" terminology

**Lines changed:** 15, 27, 41

### 3. `backend/go-server/database.go`
**Changes:**
- Updated `addMailToDB` function parameter from `userID` to `emailAccountID`
- Changed duplicate check query column from `user_id` to `email_account_id`
- Changed INSERT statement column from `user_id` to `email_account_id`
- Updated log messages to use "email_account_id" terminology

**Lines changed:** 10, 13, 19, 26

## Python-Go Integration Flow

### Request Flow (Python → Go):
```python
# Python (app.py line 335-341)
response = requests.post("http://localhost:8001/fetch-emails",
    json={
        "email_account_id": email_account_id,  # ✅ Now matches Go struct
        "mail_ids": ids
    },
    timeout=1000
)
```

### Go Server Receives:
```go
// Go (main.go line 35-38)
type messageIDs struct {
    EmailAccountID int      `json:"email_account_id"`  // ✅ Matches Python
    MailIDs        []string `json:"mail_ids"`
}
```

### Database Queries:
```go
// Go (auth.go line 27)
// ✅ Correct table and column names
row := db.QueryRow("SELECT ... FROM email_tokens WHERE email_account_id = ?", emailAccountID)

// Go (database.go line 13)
// ✅ Correct column name
db.QueryRow("SELECT COUNT(*) FROM emails WHERE message_id = ? AND email_account_id = ?", ...)

// Go (database.go line 26)
// ✅ Correct column name
db.Exec("INSERT INTO emails (email_account_id, ...) VALUES (?, ...)", emailAccountID, ...)
```

## Database Schema (Current)

### `email_tokens` table:
```sql
CREATE TABLE email_tokens (
    id INTEGER PRIMARY KEY,
    email_account_id INTEGER UNIQUE NOT NULL,  -- ✅ Foreign key to email_accounts
    access_token TEXT NOT NULL,
    refresh_token TEXT,
    token_uri VARCHAR(255),
    client_id VARCHAR(255),
    client_secret VARCHAR(255),
    scopes TEXT,
    expiry DATETIME,
    created_at DATETIME,
    updated_at DATETIME,
    FOREIGN KEY (email_account_id) REFERENCES email_accounts(id)
);
```

### `emails` table:
```sql
CREATE TABLE emails (
    id INTEGER PRIMARY KEY,
    email_account_id INTEGER NOT NULL,  -- ✅ Foreign key to email_accounts
    message_id VARCHAR(255) NOT NULL,
    thread_id VARCHAR(255),
    subject TEXT,
    sender VARCHAR(500),
    recipient VARCHAR(500),
    date_sent DATETIME,
    snippet TEXT,
    body_text TEXT,
    body_html TEXT,
    created_at DATETIME,
    FOREIGN KEY (email_account_id) REFERENCES email_accounts(id)
);
```

## Testing
After these changes, the Go server should:
1. ✅ Successfully retrieve credentials from `email_tokens` table
2. ✅ Successfully check for duplicate emails using `email_account_id`
3. ✅ Successfully insert new emails with correct `email_account_id`
4. ✅ Return emails to Python backend for vector embedding

## Verification Steps
1. Go server is running with `go run .` (auto-reloads on file changes)
2. Try syncing emails from the frontend
3. Check Go server logs for successful credential retrieval
4. Check database to verify emails are being inserted with correct `email_account_id`

## Related Files
- `/backend/databases/database.py` - Python schema definition
- `/backend/app.py` - Python API that calls Go server
- `/backend/go-server/main.go` - Go server entry point
- `/backend/go-server/auth.go` - Credential retrieval
- `/backend/go-server/database.go` - Email insertion
