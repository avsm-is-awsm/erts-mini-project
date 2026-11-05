# dk1-t2t top-level Makefile (SPEC 13).  GNU make; on Windows run it from Git Bash with
# `mingw32-make` (CLion ships one) and put a g++ on PATH, e.g.
#   export PATH="/c/Program Files/JetBrains/CLion 2026.2.1/bin/mingw/bin:$PATH"
#
#   make setup                     venv + Python packages
#   make all-offline               everything that needs no AMD tools and no board (CI)
#   make data SCEN=<name> [EDS=1]  generate a dataset          make ref SCEN=<name>  golden outputs
#   make hls | cosim | vivado | sw  AMD flows (Vivado/HLS/SDK 2017.2)
#   make analyze RUN=<dir> SCEN=<name>   compare + plots from board outputs

SHELL := bash
SCEN ?= unit_tiny
RUN  ?= build/rtlsim/cosim_10k
SCENARIOS := unit_tiny cosim_10k day_default stress_burst stress_gc_rush edge_negative_prices \
             edge_oow edge_table_full edge_errors

ifeq ($(OS),Windows_NT)
  PY_BOOT ?= py -3
  PY := .venv/Scripts/python
  EXE := .exe
  LDEXTRA := -static-libgcc -static-libstdc++
  # override e.g. JAVA="/c/Program Files/JetBrains/CLion 2026.2.1/jbr/bin/java.exe"
  JAVA ?= java
else
  PY_BOOT ?= python3
  PY := .venv/bin/python
  EXE :=
  LDEXTRA :=
  JAVA ?= java
endif
CXX ?= g++
CXXFLAGS := -O2 -Wall -Wextra -Wno-unknown-pragmas -Wno-unused-label -Wno-missing-field-initializers
INC := -Ihls/shim -Ihls/src
SYSTEMC_HOME ?= build/ext/systemc
PLANTUML ?= tools/bin/plantuml.jar

.PHONY: setup data data-all fetch ref ref-all test-py csim csim-all test-cpp rtl-test rtl-system sysc \
        hls cosim hls-impl vivado sw sw-host-test analyze evaluate stylized docs all-offline clean help

help:
	@sed -n '1,12p' Makefile

# ------------------------------------------------------------------ Python
setup:
	$(PY_BOOT) -m venv .venv
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -r requirements.txt

data:
	$(PY) -m data.generator data/scenarios/$(SCEN).yaml $(if $(EDS),--eds,)

data-all:
	for s in $(SCENARIOS); do $(PY) -m data.generator data/scenarios/$$s.yaml || exit 1; done

fetch:
	$(PY) data/fetch_eds.py --start $(START) --end $(END)

ref:
	$(PY) ref/run_ref.py data/out/$(SCEN)

ref-all:
	for s in $(SCENARIOS); do $(PY) ref/run_ref.py data/out/$$s || exit 1; done

test-py:
	$(PY) -m pytest -q tests/test_proto.py tests/test_ref.py tests/test_generator.py tests/test_itch.py

evaluate:
	$(PY) ref/evaluate.py data/out/$(SCEN)

stylized:
	$(PY) data/validate_stylized.py data/out/$(SCEN)

# ------------------------------------------------------------------ C++ (g++ + shim)
build/tb_t2t$(EXE): hls/src/t2t_top.cpp hls/tb/tb_t2t.cpp $(wildcard hls/src/core/*.hpp hls/tb/*.hpp proto/*.hpp)
	@mkdir -p build
	$(CXX) -std=c++98 $(CXXFLAGS) $(INC) hls/src/t2t_top.cpp hls/tb/tb_t2t.cpp $(LDEXTRA) -o $@

build/test_core$(EXE): hls/tb/test_core.cpp $(wildcard hls/src/core/*.hpp proto/*.hpp)
	@mkdir -p build
	$(CXX) -std=c++98 $(CXXFLAGS) $(INC) hls/tb/test_core.cpp $(LDEXTRA) -o $@

csim: build/tb_t2t$(EXE)
	./build/tb_t2t$(EXE) data/out/$(SCEN)

csim-all: build/tb_t2t$(EXE)
	./build/tb_t2t$(EXE) tests/hand_case build
	for s in $(SCENARIOS); do ./build/tb_t2t$(EXE) data/out/$$s || exit 1; done

test-cpp: build/test_core$(EXE) build/tb_t2t$(EXE)
	./build/test_core$(EXE)
	./build/tb_t2t$(EXE) tests/hand_case build
	$(PY) -m pytest -q tests/test_xcheck.py

# ------------------------------------------------------------------ RTL (Icarus + cocotb)
rtl-test:
	$(PY) -m pytest -q rtl/tests/test_probes.py

rtl-system:
	DK1_SYS_DS=cosim_10k $(PY) -m pytest -q -s rtl/tests/test_system.py

# ------------------------------------------------------------------ SystemC architecture model
build/arch_model$(EXE): systemc/arch_model.cpp
	$(CXX) -std=c++17 -O2 -I$(SYSTEMC_HOME)/include systemc/arch_model.cpp -L$(SYSTEMC_HOME)/lib \
	    -L$(SYSTEMC_HOME)/lib-linux64 -lsystemc $(if $(filter Windows_NT,$(OS)),,-lpthread) $(LDEXTRA) -o $@

sysc: build/arch_model$(EXE)
	@mkdir -p systemc/out
	for ds in cosim_10k day_default; do for r in C:replay A:replay B:replay C:live A:live B:live; do \
	    DK1_SYSC_RUN=$$r ./build/arch_model$(EXE) data/out/$$ds systemc/params.yaml \
	    systemc/out/$${ds}_$${r/:/_}.json || exit 1; done; done
	$(PY) systemc/plot_arch.py

# ------------------------------------------------------------------ AMD tools (NEEDS Vivado/HLS/SDK 2017.2)
hls:
	$(PY) hls/scripts/hls.py --steps csim,csynth,impl --sol impl1 --ds cosim_10k
	$(PY) tools/gen_regs.py

cosim:
	$(PY) hls/scripts/hls.py --steps cosim --sol cosim1 --ds cosim_10k

vivado:
	$(PY) hw/build.py --jobs 6

sw:
	$(PY) sw/build.py

build/test_sw$(EXE): sw/host_test/test_sw.cpp hls/src/t2t_top.cpp sw/osal/osal_host.cpp $(wildcard sw/app/*.hpp sw/app/*.h sw/osal/*.hpp hls/src/core/*.hpp)
	$(CXX) -std=c++11 $(CXXFLAGS) -DT2T_DBG_PKT=64 $(INC) -Isw/app sw/host_test/test_sw.cpp hls/src/t2t_top.cpp \
	    sw/osal/osal_host.cpp $(LDEXTRA) -o $@

sw-host-test: build/test_sw$(EXE)
	./build/test_sw$(EXE) tests/hand_case $(foreach s,unit_tiny cosim_10k edge_errors edge_table_full,data/out/$(s))

# ------------------------------------------------------------------ analysis + docs
analyze:
	$(PY) tools/analyze.py $(RUN) $(if $(filter-out unit_tiny,$(SCEN)),--dataset data/out/$(SCEN),)
	$(PY) tools/parse_hls_reports.py

docs:
	"$(JAVA)" -jar $(PLANTUML) -tsvg -o ../img docs/diagrams/D*.puml

# ------------------------------------------------------------------ CI entry point
all-offline: data-all ref-all test-py test-cpp csim-all sw-host-test rtl-test docs
	@echo "all-offline: OK"

clean:
	rm -rf build hls/build
