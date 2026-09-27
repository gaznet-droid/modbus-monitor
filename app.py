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

# Buffer for the last 60 readings
history = deque(maxlen=60)

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
        val = decode_power(resp.registers)
        return val
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
      <title>Power Monitor</title>
      <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
      <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; display: flex; flex-direction: column; align-items: center; padding: 2rem; margin: 0; }
        .card { background: #1e293b; padding: 2rem; border-radius: 16px; width: 90%; max-width: 960px; box-shadow: 0 10px 25px -5px rgba(0,0,0,0.5); }
        .header-row { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 1.5rem; }
        .label { font-size: 0.85rem; letter-spacing: 0.05em; color: #94a3b8; text-transform: uppercase; font-weight: 600; }
        .stat { font-size: 3rem; font-weight: 700; color: #38bdf8; margin: 0.25rem 0 0; font-variant-numeric: tabular-nums; }
        .mode-badge { font-size: 0.9rem; padding: 0.35rem 0.75rem; border-radius: 9999px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; }
        .import-badge { background: rgba(56, 189, 248, 0.15); color: #38bdf8; }
        .export-badge { background: rgba(52, 211, 153, 0.15); color: #34d399; }
      </style>
    </head>
    <body>
      <div class="card">
        <div class="header-row">
          <div>
            <div class="label">Live Net Power</div>
            <div class="stat" id="currentVal">-- W</div>
          </div>
          <div id="modeBadge" class="mode-badge import-badge">Connecting...</div>
        </div>
        <canvas id="chart" height="110"></canvas>
      </div>

      <script>
        const ctx = document.getElementById('chart').getContext('2d');
        const chart = new Chart(ctx, {
          type: 'line',
          data: {
            labels: [],
            datasets: [{
              label: 'Watts',
              data: [],
              borderColor: '#38bdf8',
              backgroundColor: 'rgba(56, 189, 248, 0.08)',
              borderWidth: 2,
              pointRadius: 2,
              pointHoverRadius: 4,
              fill: true,
              tension: 0.1
            }]
          },
          options: {
            responsive: true,
            animation: false,
            scales: {
              x: {
                grid: { color: '#334155' },
                ticks: { color: '#64748b', maxTicksLimit: 10 }
              },
              y: {
                // Allows negative readings for solar/battery export
                grid: {
                  color: function(context) {
                    // Highlight the 0 W line distinctly
                    if (context.tick && context.tick.value === 0) {
                      return '#f59e0b'; // Amber zero baseline
                    }
                    return '#334155';
                  },
                  lineWidth: function(context) {
                    return (context.tick && context.tick.value === 0) ? 2 : 1;
                  }
                },
                ticks: { 
                  color: '#64748b', 
                  callback: v => (v > 0 ? '+' : '') + v + ' W' 
                }
              }
            },
            plugins: {
              legend: { display: false },
              tooltip: {
                callbacks: { 
                  label: ctx => (ctx.parsed.y > 0 ? 'Importing: +' : 'Exporting: ') + ctx.parsed.y + ' W' 
                }
              }
            }
          }
        });

        async function updateData() {
          try {
            const res = await fetch('/api/data');
            const data = await res.json();
            if (data.length > 0) {
              chart.data.labels = data.map(d => d.time);
              chart.data.datasets[0].data = data.map(d => d.value);

              const latest = data[data.length - 1].value;
              const valEl = document.getElementById('currentVal');
              const badgeEl = document.getElementById('modeBadge');

              if (latest < 0) {
                // Exporting (Solar/Battery generation exceeding home load)
                valEl.style.color = '#34d399'; // Emerald green
                valEl.innerText = latest + ' W';
                badgeEl.className = 'mode-badge export-badge';
                badgeEl.innerText = 'Exporting to Grid';
                chart.data.datasets[0].borderColor = '#34d399';
                chart.data.datasets[0].backgroundColor = 'rgba(52, 211, 153, 0.08)';
              } else {
                // Importing (Drawing from grid)
                valEl.style.color = '#38bdf8'; // Blue
                valEl.innerText = '+' + latest + ' W';
                badgeEl.className = 'mode-badge import-badge';
                badgeEl.innerText = 'Importing from Grid';
                chart.data.datasets[0].borderColor = '#38bdf8';
                chart.data.datasets[0].backgroundColor = 'rgba(56, 189, 248, 0.08)';
              }

              chart.update('none');
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
