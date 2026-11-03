CREATE TABLE `administrator` (
	`id` integer PRIMARY KEY NOT NULL,
	`owner` text NOT NULL,
	`username` text,
	`password_hash` text
);
--> statement-breakpoint
CREATE TABLE `login_attempts` (
	`owner` text PRIMARY KEY NOT NULL,
	`window` integer NOT NULL,
	`count` integer NOT NULL
);
--> statement-breakpoint
CREATE TABLE `previews` (
	`id` text PRIMARY KEY NOT NULL,
	`session` text NOT NULL,
	`base` text NOT NULL,
	`payload` text NOT NULL,
	`changes` text NOT NULL,
	`reason` text NOT NULL,
	`created` integer NOT NULL
);
--> statement-breakpoint
CREATE TABLE `revisions` (
	`id` text PRIMARY KEY NOT NULL,
	`parent` text,
	`actor` text NOT NULL,
	`reason` text NOT NULL,
	`created` integer NOT NULL,
	`payload` text NOT NULL,
	`changes` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `sessions` (
	`id` text PRIMARY KEY NOT NULL,
	`owner` text NOT NULL,
	`csrf` text NOT NULL,
	`created` integer NOT NULL,
	`touched` integer NOT NULL
);
--> statement-breakpoint
CREATE TABLE `tournament` (
	`id` integer PRIMARY KEY NOT NULL,
	`revision` text NOT NULL,
	`payload` text NOT NULL
);
