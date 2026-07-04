
all:
	@echo -n "Git update: "
	@git pull
	@python3 generator/build_site.py --datadir data --outdir _site
