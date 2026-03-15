.PHONY: help status interview scaffold _engineering_skeleton

# Default target
help:
	@echo ""
	@echo "Venture Template"
	@echo "================"
	@echo ""
	@echo "  make status     Show current phase, decisions, and next action"
	@echo "  make interview  Start AI-guided interview to fill in all phase docs"
	@echo "  make scaffold   Generate 05-engineering/spec.md and bootstrap code skeleton"
	@echo ""

# ─── Status ──────────────────────────────────────────────────────────────────

status:
	@echo ""
	@echo "=== Phase ==="
	@grep "^Current Phase:" CLAUDE.md 2>/dev/null | sed 's/Current Phase: /  /' || echo "  (not set — update CLAUDE.md)"
	@echo ""
	@echo "=== Next Action ==="
	@awk '/^## Next Action/{found=1; next} found && /^## /{exit} found && NF{print "  " $$0}' CLAUDE.md 2>/dev/null || echo "  (none set)"
	@echo ""
	@echo "=== Phase Docs ==="
	@for f in \
		"00-discovery/brief.md" \
		"01-strategy/business-case.md" \
		"02-product/prd.md" \
		"03-design/design-brief.md" \
		"04-data/data-plan.md" \
		"05-engineering/spec.md"; do \
		if [ ! -f "$$f" ]; then \
			printf "  [ ] $$f\n"; \
		elif grep -q "<!-- " "$$f" 2>/dev/null; then \
			printf "  [~] $$f  (has unfilled sections)\n"; \
		else \
			printf "  [x] $$f\n"; \
		fi; \
	done
	@echo ""
	@echo "=== Key Decisions ==="
	@awk '/^## Key Decisions/{found=1; next} found && /^## /{exit} found && /^-/{print "  " $$0}' CLAUDE.md 2>/dev/null | head -10
	@echo ""

# ─── Interview ───────────────────────────────────────────────────────────────

interview:
	@test -f pitch.md || { echo "ERROR: pitch.md not found."; exit 1; }
	@grep -q "<!-- Replace this comment" pitch.md && { echo "ERROR: pitch.md is still the default template. Add your idea first."; exit 1; } || true
	@test -s pitch.md || { echo "ERROR: pitch.md is empty. Add your idea first."; exit 1; }
	@echo ""
	@echo "Starting interview session..."
	@echo "Claude will read your pitch and .claude/guides/interview.md, then guide you through the phases."
	@echo ""
	@claude "Please read the file .claude/guides/interview.md for your instructions, then read pitch.md for the founder's idea. Follow the instructions in interview.md to conduct the interview."

# ─── Scaffold ────────────────────────────────────────────────────────────────

scaffold:
	@echo ""
	@echo "Checking prerequisites..."
	@test -f 02-product/prd.md || { echo "ERROR: 02-product/prd.md not found. Complete the product phase first."; exit 1; }
	@test -f 04-data/data-plan.md || { echo "ERROR: 04-data/data-plan.md not found. Complete the data phase first."; exit 1; }
	@mkdir -p 05-engineering/src
	@echo ""
	@echo "Generating 05-engineering/spec.md..."
	@if command -v claude >/dev/null 2>&1; then \
		echo "Using Claude to generate spec from phase docs..."; \
		PRD=$$(cat 02-product/prd.md); \
		DATAPLAN=$$(cat 04-data/data-plan.md); \
		DESIGN=$$(cat 03-design/design-brief.md 2>/dev/null || echo "No design brief available."); \
		PROMPT="You are a senior software engineer. Generate a technical architecture spec for a new product. Be concrete and opinionated — pick a specific stack, define real data models with field names, define API routes with HTTP verbs and request/response shapes. The deployment target is Docker containers — include Docker in the infrastructure section.\n\nOutput ONLY a markdown document starting with '# Technical Spec'. Use exactly these sections:\n## Overview\n## Stack\n## Architecture\n## Data Model\n## API Design\n## Infrastructure\n## Open Questions\n\n---\n\nPRD:\n$$PRD\n\n---\n\nDesign Brief:\n$$DESIGN\n\n---\n\nData Plan:\n$$DATAPLAN"; \
		printf '%s' "$$PROMPT" | claude -p > 05-engineering/spec.md && echo "spec.md generated." || { echo "Claude CLI call failed — writing template instead."; $(MAKE) -s _write_spec_template; }; \
	else \
		echo "Claude CLI not found — writing spec template for you to fill in."; \
		$(MAKE) -s _write_spec_template; \
	fi
	@echo ""
	@echo "Bootstrapping engineering scaffold..."
	@$(MAKE) -s _engineering_skeleton
	@echo ""
	@echo "Done. Next steps:"
	@echo "  1. Review 05-engineering/spec.md — edit stack/architecture as needed"
	@echo "  2. Update CLAUDE.md with stack choices and any new decisions"
	@echo "  3. Run /build in Claude Code to decompose spec into the task backlog"
	@echo "  4. Run /build [task-name] to implement each task with the right specialist"
	@echo ""

_write_spec_template:
	@printf '# Technical Spec\n\n> Generated from PRD and data plan. Fill in each section.\n> Reference 02-product/prd.md (Engineering Handoff) and 04-data/data-plan.md.\n\n## Overview\n\n<!-- One paragraph: what this system does and its key constraints -->\n\n## Stack\n\n- Frontend:\n- Backend:\n- Database:\n- Auth:\n- Hosting:\n- Analytics:\n\n## Architecture\n\n<!-- Describe the major components and how they interact.\n     A simple diagram in ASCII is fine. -->\n\n## Data Model\n\n<!-- Define the primary tables/collections with key fields.\n     Example:\n     ### users\n     - id: uuid PK\n     - email: text unique\n     - created_at: timestamp\n-->\n\n## API Design\n\n<!-- Key routes with HTTP method, path, and brief description.\n     Example:\n     - POST /api/auth/login\n     - GET  /api/projects\n     - POST /api/projects\n-->\n\n## Infrastructure\n\n<!-- Deployment, CI/CD, environments -->\n\n- Dev: local Docker\n- Staging:\n- Production:\n- CI:\n\n## Open Questions\n\n-\n' > 05-engineering/spec.md

_engineering_skeleton:
	@if [ ! -f 05-engineering/Makefile ]; then \
		printf '.PHONY: dev test build\n\ndev: ## Start development server\n\t@echo "Add your dev command here"\n\ntest: ## Run tests\n\t@echo "Add your test command here"\n\nbuild: ## Build for production\n\t@echo "Add your build command here"\n' > 05-engineering/Makefile; \
		echo "  created 05-engineering/Makefile"; \
	fi
	@if [ ! -f 05-engineering/Dockerfile ]; then \
		printf '# TODO: Replace with your stack base image\n# python:3.12-slim | node:20-alpine | ruby:3.3-slim | golang:1.22-alpine\nFROM python:3.12-slim\n\nWORKDIR /app\n\n# TODO: copy dependency manifest and install\n# COPY requirements.txt .\n# RUN pip install --no-cache-dir -r requirements.txt\n\nCOPY . .\n\n# TODO: update port to match your app\nEXPOSE 8000\n\n# TODO: replace with your start command\nCMD ["echo", "replace this CMD with your app start command"]\n' > 05-engineering/Dockerfile; \
		echo "  created 05-engineering/Dockerfile"; \
	fi
	@if [ ! -f 05-engineering/docker-compose.yml ]; then \
		printf 'services:\n  app:\n    build: .\n    ports:\n      - "8000:8000"\n    env_file: .env\n    depends_on:\n      db:\n        condition: service_healthy\n    develop:\n      watch:\n        - action: sync\n          path: ./src\n          target: /app/src\n\n  db:\n    image: postgres:16-alpine\n    environment:\n      POSTGRES_DB: app\n      POSTGRES_USER: app\n      POSTGRES_PASSWORD: password\n    ports:\n      - "5432:5432"\n    volumes:\n      - db_data:/var/lib/postgresql/data\n    healthcheck:\n      test: ["CMD-SHELL", "pg_isready -U app"]\n      interval: 5s\n      timeout: 5s\n      retries: 5\n\n  # redis:\n  #   image: redis:7-alpine\n  #   ports:\n  #     - "6379:6379"\n\nvolumes:\n  db_data:\n' > 05-engineering/docker-compose.yml; \
		echo "  created 05-engineering/docker-compose.yml"; \
	fi
	@if [ ! -f 05-engineering/.gitignore ]; then \
		printf '.env\n*.log\n.DS_Store\n__pycache__/\n*.pyc\n*.pyo\nnode_modules/\ndist/\nbuild/\n.cache/\n*.egg-info/\n.venv/\n' > 05-engineering/.gitignore; \
		echo "  created 05-engineering/.gitignore"; \
	fi
	@if [ ! -f 05-engineering/src/.gitkeep ]; then \
		touch 05-engineering/src/.gitkeep; \
		echo "  created 05-engineering/src/"; \
	fi
