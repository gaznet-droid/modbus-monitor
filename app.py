import asyncio
import struct
from datetime import datetime
from collections import deque
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pymodbus.client import ModbusTcpClient

MODBUS_IP = "2.98.153.184"
MODBUS_PORT = 8838
SLAVE_ID = 1
REGISTER_ADDR = 146
POLL_INTERVAL = 3

# Keep up to 5 hours in memory so 4-hour window works seamlessly
history = deque(maxlen=6000)

def decode_power(registers):
    raw_bytes = struct.pack('>HH', registers[0], registers[1])
    return round(struct.unpack('>f', raw_bytes)[0], 2)

def read_power():
    client = ModbusTcpClient(MODBUS_IP, port=MODBUS_PORT, timeout=3)
    if not client.connect():
        return None
    try:
        resp = client.read_input_registers(address=REGISTER_ADDR, count=2, slave=SLAVE_ID)
        if resp.isError():
            return None
        return decode_power(resp.registers)
    except Exception:
        return None
    finally:
        client.close()

async def poll_device_loop():
    while True:
        val = await asyncio.to_thread(read_power)
        if val is not None:
            history.append({
                "time": datetime.now().strftime("%H:%M:%S"),
                "value": val
            })
        await asyncio.sleep(POLL_INTERVAL)

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(poll_device_loop())
    yield
    task.cancel()

app = FastAPI(lifespan=lifespan)

@app.get("/api/data")
def get_data():
    return list(history)

@app.get("/", response_class=HTMLResponse)
def index():
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
      <title>Live Power Monitor</title>
      <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
      <style>
        :root {
          --bg-base: #090d16;
          --card-bg: #141c2e;
          --border: #222f49;
          --text-main: #f8fafc;
          --text-muted: #94a3b8;
          --import-color: #38bdf8;
          --export-color: #34d399;
          --amber-zero: #f59e0b;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
          background: var(--bg-base);
          color: var(--text-main);
          display: flex;
          flex-direction: column;
          align-items: center;
          padding: 1rem;
          min-height: 100vh;
        }

        .container {
          width: 100%;
          max-width: 900px;
          display: flex;
          flex-direction: column;
          gap: 1.25rem;
        }

        .card {
          background: var(--card-bg);
          border: 1px solid var(--border);
          border-radius: 16px;
          padding: 1.25rem;
          box-shadow: 0 10px 25px -5px rgba(0,0,0,0.5);
        }

        .chart-card {
          display: flex;
          flex-direction: column;
          min-height: 48vh;
        }

        .header-row {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 0.75rem;
          gap: 0.5rem;
        }

        .label {
          font-size: 0.75rem;
          letter-spacing: 0.06em;
          color: var(--text-muted);
          text-transform: uppercase;
          font-weight: 700;
        }

        .stat {
          font-size: 2.2rem;
          font-weight: 700;
          color: var(--import-color);
          line-height: 1.1;
          font-variant-numeric: tabular-nums;
        }

        .controls-row {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 0.5rem;
        }

        .time-select {
          background: #1e293b;
          color: var(--text-main);
          border: 1px solid var(--border);
          border-radius: 8px;
          padding: 0.3rem 0.6rem;
          font-size: 0.75rem;
          font-weight: 600;
          outline: none;
        }

        .mode-badge {
          font-size: 0.75rem;
          padding: 0.4rem 0.8rem;
          border-radius: 9999px;
          font-weight: 700;
          text-transform: uppercase;
          letter-spacing: 0.04em;
          white-space: nowrap;
        }

        .import-badge { background: rgba(56, 189, 248, 0.15); color: var(--import-color); border: 1px solid rgba(56, 189, 248, 0.3); }
        .export-badge { background: rgba(52, 211, 153, 0.15); color: var(--export-color); border: 1px solid rgba(52, 211, 153, 0.3); }

        .chart-wrapper {
          position: relative;
          flex: 1;
          width: 100%;
          min-height: 220px;
        }

        .donut-card {
          display: flex;
          flex-direction: column;
          align-items: center;
        }

        .donut-header {
          width: 100%;
          text-align: left;
          margin-bottom: 1rem;
        }

        .donut-container {
          display: flex;
          flex-direction: row;
          align-items: center;
          justify-content: space-around;
          width: 100%;
          gap: 1rem;
        }

        .donut-chart-box {
          position: relative;
          width: 160px;
          height: 160px;
        }

        .legend-box {
          display: flex;
          flex-direction: column;
          gap: 0.75rem;
        }

        .legend-item {
          display: flex;
          flex-direction: column;
        }

        .legend-label {
          font-size: 0.75rem;
          color: var(--text-muted);
          text-transform: uppercase;
          font-weight: 600;
          display: flex;
          align-items: center;
          gap: 0.35rem;
        }

        .indicator {
          width: 8px;
          height: 8px;
          border-radius: 50%;
          display: inline-block;
        }

        .legend-val {
          font-size: 1.15rem;
          font-weight: 700;
          font-variant-numeric: tabular-nums;
        }

        @media (min-width: 600px) {
          body { padding: 2rem; }
          .stat { font-size: 3rem; }
          .label { font-size: 0.85rem; }
          .mode-badge { font-size: 0.85rem; padding: 0.45rem 1rem; }
          .chart-wrapper { min-height: 280px; }
          .donut-chart-box { width: 190px; height: 190px; }
        }
      </style>
    </head>
    <body>
      <div class="container">
        <div class="card chart-card">
          <div class="header-row">
            <div>
              <div class="label">Live Net Power</div>
              <div class="stat" id="currentVal">-- W</div>
            </div>
            <div id="modeBadge" class="mode-badge import-badge">Connecting...</div>
          </div>
          
          <div class="controls-row">
            <span class="label">Time Window</span>
            <select id="timeWindow" class="time-select">
              <option value="900">15 Mins</option>
              <option value="1800">30 Mins</option>
              <option value="3600">1 Hour</option>
              <option value="7200">2 Hours</option>
              <option value="14400" selected>4 Hours</option>
            </select>
          </div>

          <div class="chart-wrapper">
            <canvas id="lineChart"></canvas>
          </div>
        </div>

        <div class="card donut-card">
          <div class="donut-header">
            <div class="label">Session Energy Split</div>
          </div>
          <div class="donut-container">
            <div class="donut-chart-box">
              <canvas id="donutChart"></canvas>
            </div>
            <div class="legend-box">
              <div class="legend-item">
                <span class="legend-label">
                  <span class="indicator" style="background: var(--import-color)"></span> Imported
                </span>
                <span class="legend-val" id="totalImportVal" style="color: var(--import-color)">0 Wh</span>
              </div>
              <div class="legend-item">
                <span class="legend-label">
                  <span class="indicator" style="background: var(--export-color)"></span> Exported
                </span>
                <span class="legend-val" id="totalExportVal" style="color: var(--export-color)">0.00 Wh</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <script>
        const lineCtx = document.getElementById('lineChart').getContext('2d');
        const isMobile = window.innerWidth < 600;

        const lineChart = new Chart(lineCtx, {
          type: 'line',
          data: {
            labels: [],
            datasets: [{
              label: 'Watts',
              data: [],
              borderColor: '#38bdf8',
              backgroundColor: 'rgba(56, 189, 248, 0.08)',
              borderWidth: 2,
              pointRadius: 0,
              pointHoverRadius: 4,
              fill: true,
              tension: 0.15
            }]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            scales: {
              x: {
                grid: { color: '#1e293b' },
                ticks: {
                  color: '#94a3b8',
                  font: { size: isMobile ? 11 : 12, weight: '600' },
                  maxTicksLimit: isMobile ? 6 : 10
                }
              },
              y: {
                grid: {
                  color: function(context) {
                    if (context.tick && context.tick.value === 0) {
                      return '#f59e0b';
                    }
                    return '#1e293b';
                  },
                  lineWidth: function(context) {
                    return (context.tick && context.tick.value === 0) ? 2 : 1;
                  }
                },
                ticks: {
                  color: '#94a3b8',
                  font: { size: isMobile ? 12 : 13, weight: '700' },
                  callback: v => (v < 0 ? Number(v).toFixed(2) : (v > 0 ? '+' + v : v)) + ' W'
                }
              }
            },
            plugins: {
              legend: { display: false },
              tooltip: {
                callbacks: {
                  label: ctx => {
                    const y = ctx.parsed.y;
                    return y < 0 
                      ? 'Exporting: ' + Number(y).toFixed(2) + ' W'
                      : 'Importing: +' + y + ' W';
                  }
                }
              }
            }
          }
        });

        const donutCtx = document.getElementById('donutChart').getContext('2d');
        let sessionImportWh = 0;
        let sessionExportWh = 0;
        let lastTimestamp = null;

        const donutChart = new Chart(donutCtx, {
          type: 'doughnut',
          data: {
            labels: ['Imported', 'Exported'],
            datasets: [{
              data: [0.001, 0],
              backgroundColor: ['#38bdf8', '#34d399'],
              borderColor: '#141c2e',
              borderWidth: 3,
              hoverOffset: 4
            }]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '70%',
            plugins: {
              legend: { display: false }
            }
          }
        });

        async function updateData() {
          try {
            const res = await fetch('/api/data');
            const data = await res.json();
            if (data.length > 0) {
              const windowSeconds = parseInt(document.getElementById('timeWindow').value);
              const maxPoints = Math.floor(windowSeconds / 3);
              const slicedData = data.slice(-maxPoints);

              lineChart.data.labels = slicedData.map(d => d.time);
              lineChart.data.datasets[0].data = slicedData.map(d => d.value);

              const latest = data[data.length - 1];
              const val = latest.value;
              const valEl = document.getElementById('currentVal');
              const badgeEl = document.getElementById('modeBadge');

              if (val < 0) {
                valEl.style.color = '#34d399';
                valEl.innerText = Number(val).toFixed(2) + ' W';
                badgeEl.className = 'mode-badge export-badge';
                badgeEl.innerText = 'Exporting';
                lineChart.data.datasets[0].borderColor = '#34d399';
                lineChart.data.datasets[0].backgroundColor = 'rgba(52, 211, 153, 0.08)';
              } else {
                valEl.style.color = '#38bdf8';
                valEl.innerText = '+' + val + ' W';
                badgeEl.className = 'mode-badge import-badge';
                badgeEl.innerText = 'Importing';
                lineChart.data.datasets[0].borderColor = '#38bdf8';
                lineChart.data.datasets[0].backgroundColor = 'rgba(56, 189, 248, 0.08)';
              }

              lineChart.update('none');

              const now = Date.now();
              if (lastTimestamp) {
                const deltaHours = (now - lastTimestamp) / 3600000;
                if (val >= 0) {
                  sessionImportWh += (val * deltaHours);
                } else {
                  sessionExportWh += (Math.abs(val) * deltaHours);
                }

                donutChart.data.datasets[0].data = [sessionImportWh, sessionExportWh];
                donutChart.update('none');

                document.getElementById('totalImportVal').innerText = sessionImportWh.toFixed(1) + ' Wh';
                document.getElementById('totalExportVal').innerText = sessionExportWh.toFixed(2) + ' Wh';
              }
              lastTimestamp = now;
            }
          } catch (e) {
            console.error(e);
          }
        }

        setInterval(updateData, 2000);
        updateData();
      </script>
    </body>
    </html>
    """

if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port)
