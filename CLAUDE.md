# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Hummingbot is an open-source Python-based framework for building and deploying algorithmic trading bots. The framework supports trading on 140+ exchanges (both CEX and DEX) and includes both traditional strategies and a new Strategy V2 architecture with Controllers and Executors.

## Development Commands

### Environment Setup
- `./install` - Install dependencies using Anaconda
- `./compile` - Compile Cython extensions
- `./clean` - Clean up compiled files and build artifacts

### Running
- `./start` - Start Hummingbot with options: `-p <password>`, `-f <file>`, `-c <config>`
- `make run-v2` - Run Strategy V2 with controllers

### Testing
- `make test` - Run pytest with coverage
- Pre-commit hooks are installed automatically with `./install`

## Tips to always keep in mind
The architecture already has robust and well implemented code for the following -

Managing Exchange Connections
Interacting With Exchanges
Fetching Candles,Orderbooks and all other kinds of Api Data from Exchanges
Order Lifecycle management
Position Management and tracking
Event Tracking for all important events throughtout trading, like order fills, cancellations, funding payments,

## Web3 Blockchain Integration
- Already offers robust web3 trading through Gateway connector
- Gateway is a complementing rest api based codebase that helps interact with various web3 blockchains and has endpoints for connected web3 account and dex related operations

## Strategy V2 Architecture

### Controllers (`hummingbot/strategy_v2/controllers/`)
- **Purpose**: High-level strategy logic and decision making
- **Base Class**: `ControllerBase` extends `RunnableBase`
- **Configuration**: Uses Pydantic models extending `ControllerConfigBase`

### Executors (`hummingbot/strategy_v2/executors/`)
- **Purpose**: Execute specific trading actions (orders, positions)
- **Base Class**: `ExecutorBase` extends `RunnableBase`
- **Orchestration**: `ExecutorOrchestrator` manages multiple executors

### Connectors (todo)
- **Purpose**: Execute specific trading actions (orders, positions)
- **Base Class**: `ExecutorBase` extends `RunnableBase`

### Order Management
Classes Involved

Reference Lookup when working with scripts, controllers or executors

position_executor.py : for order placement/management and position management
dca_executor.py : for managing multiple orders and positions
marker_making_controller_base.py : for market making related controllers
directional_trading_controller_base.py: for directional trading related controllers
