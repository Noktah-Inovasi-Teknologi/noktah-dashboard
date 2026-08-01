-- Migration: 004_relational_spine (down)
-- Drops only the tables 004 introduced, in FK-safe (dependents-first) order.
-- Affects no data that existed before this migration (FR-027) — none of these
-- tables existed prior to 004.

BEGIN;

DROP TABLE IF EXISTS briefs;
DROP TABLE IF EXISTS account_follower_observations;
DROP TABLE IF EXISTS runs;
DROP TABLE IF EXISTS client_account_roles;
DROP TABLE IF EXISTS account_handles;
DROP TABLE IF EXISTS accounts;
DROP TABLE IF EXISTS client_aliases;
DROP TABLE IF EXISTS clients;

DELETE FROM schema_migrations WHERE version = '004_relational_spine';

COMMIT;
