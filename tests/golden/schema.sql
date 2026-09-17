CREATE TABLE accounts (
	id INTEGER NOT NULL, 
	primary_email VARCHAR(255) NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	created_at DATETIME, 
	updated_at DATETIME, 
	PRIMARY KEY (id), 
	UNIQUE (primary_email)
);
CREATE TABLE email_accounts (
	id INTEGER NOT NULL, 
	account_id INTEGER NOT NULL, 
	email VARCHAR(255) NOT NULL, 
	provider VARCHAR(50) NOT NULL, 
	is_primary INTEGER NOT NULL, 
	created_at DATETIME, 
	updated_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(account_id) REFERENCES accounts (id), 
	UNIQUE (email)
);
CREATE TABLE email_tokens (
	id INTEGER NOT NULL, 
	email_account_id INTEGER NOT NULL, 
	access_token TEXT NOT NULL, 
	refresh_token TEXT, 
	token_uri VARCHAR(255), 
	client_id VARCHAR(255), 
	client_secret VARCHAR(255), 
	scopes TEXT, 
	expiry DATETIME, 
	created_at DATETIME, 
	updated_at DATETIME, 
	PRIMARY KEY (id), 
	UNIQUE (email_account_id), 
	FOREIGN KEY(email_account_id) REFERENCES email_accounts (id)
);
CREATE TABLE emails (
	id INTEGER NOT NULL, 
	email_account_id INTEGER NOT NULL, 
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
	PRIMARY KEY (id), 
	FOREIGN KEY(email_account_id) REFERENCES email_accounts (id)
);
