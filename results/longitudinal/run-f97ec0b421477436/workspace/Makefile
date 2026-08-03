.PHONY: bench

bench:
ifeq ($(REMOTE),1)
	./bench/boxrun.sh $(ALGO)
else
	python3 bench/run_bench.py $(ALGO)
endif
