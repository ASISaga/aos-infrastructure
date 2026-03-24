# Contributing to aos-infrastructure

**Last Updated**: 2026-03-24

## Prerequisites

- Python 3.10+
- Azure CLI (`az`) with Bicep extension (`az bicep install`)
- Git

## Setup

```bash
git clone https://github.com/ASISaga/aos-infrastructure.git
cd aos-infrastructure

# Install all dependencies (including dev tools)
pip install -e ".[dev]"
```

## Project Structure

```
deployment/
├── deploy.py               # CLI entry point (28 subcommands)
├── main-modular.bicep      # Primary Bicep template (15 direct modules)
├── modules/                # 20 Bicep modules
├── parameters/             # Environment-specific parameter files
├── orchestrator/           # Python orchestrator (Governance, Automation, Reliability)
├── workflow-templates/     # GitHub Actions templates for code repositories
└── tests/                  # 123+ unit tests
docs/                       # Documentation
.github/
├── workflows/              # 8 GitHub Actions workflows
├── specs/                  # Repository specification
└── instructions/           # Copilot agent path-specific instructions
```

## Development Workflow

1. Create a feature branch from `main`
2. Make changes
3. Run tests: `pytest deployment/tests/ -v`
4. Validate Bicep: `az bicep build --file deployment/main-modular.bicep --stdout`
5. Run Python linting: `pylint deployment/orchestrator/`
6. Commit with conventional commit messages
7. Open a pull request

## Testing

```bash
# Run all tests
pytest deployment/tests/ -v

# Run with coverage
pytest deployment/tests/ --cov=deployment

# Run specific test module
pytest deployment/tests/test_manager.py -v
pytest deployment/tests/test_ooda_loop.py -v
```

## Linting

```bash
# Python linting (max line length 120)
pylint deployment/orchestrator/ --fail-under=7.0

# Bicep linting
az bicep build --file deployment/main-modular.bicep --stdout
```

## Adding a New Module

1. Create `deployment/modules/<name>.bicep`
2. Add a `module <name> 'modules/<name>.bicep'` declaration in `main-modular.bicep`
3. Update `docs/architecture.md` Bicep module list
4. Update `.github/specs/repository.md` module count and directory listing
5. Add unit tests in `deployment/tests/` if the module adds Python orchestrator logic

## Adding a New Code Repository Deployment Template

1. Copy the most similar template from `deployment/workflow-templates/`
2. Update `app-name` and comment headers
3. If it's a Function App: verify the app name matches `appNames` (or `mcpServerApps`) in `main-modular.bicep`
4. If it's a Foundry Agent: verify the agent name matches `foundryAppNames` in `main-modular.bicep`
5. Update `deployment/workflow-templates/README.md`

## Code Style

- Python 3.10+ with type hints on all public functions
- Line length: 120 characters maximum (both Python and comments)
- Use `async def` for I/O-bound operations
- Follow PEP 8 naming conventions
- Use double-quoted strings
- Imports: stdlib → third-party → local, separated by blank lines
- Write Google-style docstrings for public functions and classes
- Never use `print()` — use `logging.getLogger(__name__)`

## Commit Messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add regional validation for swedencentral
fix: correct health check timeout calculation
docs: update deployment architecture diagram
chore: upgrade pydantic to 2.12
refactor: extract OODA loop into separate class
test: add tests for ScaleDownAuditor
```

## Pull Request Checklist

- [ ] Tests pass: `pytest deployment/tests/ -v`
- [ ] Bicep validates: `az bicep build --file deployment/main-modular.bicep --stdout`
- [ ] Pylint passes: `pylint deployment/orchestrator/ --fail-under=7.0`
- [ ] `docs/` updated if architecture, APIs, or workflows changed
- [ ] `.github/specs/repository.md` updated if directory structure changed
- [ ] Conventional commit message used
- [ ] No secrets committed

## References

→ **Repository spec**: `.github/specs/repository.md`  
→ **Architecture guide**: `docs/architecture.md`  
→ **Workflows guide**: `docs/workflows.md`  
→ **Python coding standards**: `.github/instructions/python.instructions.md`
