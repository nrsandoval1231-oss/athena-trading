"""Athena Core adapter boundary for trading.

Keep this module domain-facing: Athena Trading may translate trading observations,
decisions and outcomes into generic Athena episodes, but Athena Core must not import
market, broker, ticker, ORB, or PnL primitives from this repository.
"""
