VENV_PATH?=./venv

clean:
	rm -rf build dist venv

venv:
	python3 -m venv "$(VENV_PATH)"
	"$(VENV_PATH)"/bin/pip install --upgrade wheel
	"$(VENV_PATH)"/bin/pip install --editable .
	# The --editable option seems to prevent data_files from being installed
	mkdir -p venv/share/txs/lua
	ln -s ../../../../txs-compare.lua venv/share/txs/lua
