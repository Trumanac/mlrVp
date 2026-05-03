@echo off
REM Launch mlrVp using the legacy monomeserial OSC bridge.
REM monomeserial defaults: host=127.0.0.1 host_port=8000 listen_port=8080

set MLRVP_MONOMESERIAL=1
set MLRVP_MONOMESERIAL_HOST=127.0.0.1
set MLRVP_MONOMESERIAL_RECV_HOST=127.0.0.1
set MLRVP_MONOMESERIAL_RECV_PORT=8000
set MLRVP_MONOMESERIAL_SEND_PORT=8080

call run.bat