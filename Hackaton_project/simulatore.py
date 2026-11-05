"""
Drone simulato per provare centrale.py e giro_punti.py senza hardware e senza Internet.

DroneSimulato risponde agli stessi metodi di djitellopy.Tello usati da ispezione.py e si muove sulla pianta:
il sensore ToF guarda gli ostacoli veri (più quelli extra non presenti nella pianta) e un urto viene registrato.
"""
import math
import random
import re
import time

import cv2
import numpy as np

HFOV_GRADI = 70.0
FATTORE_TEMPO = 0.02            # 1 s di volo simulato = 0,02 s reali


class _Lettore:
    def __init__(self, drone):
        self.drone = drone

    @property
    def frame(self):
        d = self.drone
        img = np.full((720, 960, 3), 40, np.uint8)
        testo = [f"SIMULATORE  {'BASSA' if d.bassa else 'FRONTALE'}",
                 f"x {d.x:.2f}  y {d.y:.2f}  dir {d.dir:.0f}  quota {d.quota:.0f} cm",
                 f"batteria {d.batteria:.0f}%"]
        if d.anomalia and not d.bassa:
            dx, dy = d.anomalia[0] - d.x, d.anomalia[1] - d.y
            relativo = (math.degrees(math.atan2(dy, dx)) - d.dir + 180) % 360 - 180
            if abs(relativo) < HFOV_GRADI / 2:
                u = 0.5 - relativo / HFOV_GRADI
                cv2.circle(img, (int(u * 960), 500), 40, (0, 0, 255), -1)
                testo.append(f"ANOMALIA a {math.hypot(dx, dy):.1f} m")
        for i, t in enumerate(testo):
            cv2.putText(img, t, (30, 60 + 50 * i), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 2)
        return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)       # come il Tello: fotogrammi RGB


class DroneSimulato:
    def __init__(self, mappa, base, anomalia=None, ostacoli_extra=(), rumore=0.0, batteria=90, seme=1):
        self.mappa, self.anomalia = mappa, anomalia
        self.x, self.y, self.dir = base
        self.base = base
        self.ostacoli = list(ostacoli_extra)              # [(x, y, raggio)] non presenti nella pianta
        self.rumore = rumore
        self.batteria = batteria
        self.yaw = 0.0
        self.quota = 0.0
        self.in_volo = self.bassa = False
        self.urti = []
        self.comandi = []
        self._risposte = []
        self._caso = random.Random(seme)

    # --- metodi di djitellopy usati da ispezione.py ---
    def connect(self):
        pass

    def end(self):
        pass

    def streamon(self):
        self.comandi.append("streamon")

    def streamoff(self):
        self.comandi.append("streamoff")

    def get_frame_read(self):
        return _Lettore(self)

    def get_own_udp_object(self):
        return {"responses": self._risposte}

    def emergency(self):
        self.comandi.append("emergency")
        self.in_volo, self.quota = False, 0.0

    def land(self):
        self.send_command_with_return("land")

    def takeoff(self):
        risposta = self.send_command_with_return("takeoff")
        if not risposta.startswith("ok"):
            raise RuntimeError(risposta)

    def enable_mission_pads(self):
        return self.send_command_with_return("mon")

    def set_mission_pad_detection_direction(self, direzione):
        return self.send_command_with_return(f"mdirection {direzione}")

    def get_battery(self):
        return int(self.batteria)

    # --- volo continuo (rc): il drone si muove nel tempo reale alla velocità comandata ---
    CM_S_PER_RC = 4.0                                        # 50 -> 2 m/s: prove veloci

    def send_rc_control(self, lr, fb, ud, yaw):
        self._integra_rc()
        self._rc = (lr, fb, ud, yaw)
        self.comandi.append(f"rc {lr} {fb} {ud} {yaw}")

    def _integra_rc(self):
        ora = time.time()
        dt, self._t_rc = ora - getattr(self, "_t_rc", ora), ora
        if self.in_volo and dt > 0:
            self.batteria -= 0.15 * dt                       # in volo circa il 9% al minuto, come un Tello
        _, fb, ud, _ = getattr(self, "_rc", (0, 0, 0, 0))
        if ud and self.in_volo and dt > 0:                   # frecce su/giù: salita e discesa con rc
            self.quota = max(10.0, self.quota + ud * self.CM_S_PER_RC * dt)
        self._v_cm_s = fb * self.CM_S_PER_RC if self.in_volo else 0.0
        if self._v_cm_s and dt > 0:
            if self._muovi(self._v_cm_s * dt / 100, 0, attesa=False) != "ok":
                self._rc = (0, 0, 0, 0)

    def get_mission_pad_id(self):
        return self.get_current_state()["mid"]

    def get_current_state(self):
        raggio_visibile = max(0.2, 0.45 * self.quota / 100)          # più in alto vede un'area più larga
        sopra_pad = math.dist((self.x, self.y), self.base[:2]) < raggio_visibile and 30 <= self.quota <= 120
        self._integra_rc()
        v_dm_s = (getattr(self, "_v_cm_s", 0.0) + getattr(self, "_v_passo", 0.0)) / 10   # come il Tello: dm/s
        return {"yaw": int(round((self.yaw + 180) % 360 - 180)), "tof": int(self.quota) if self.in_volo else 10,
                "vgx": v_dm_s, "vgy": 0.0,
                "h": int(self.quota), "bat": int(self.batteria), "templ": 58, "temph": 62,
                "mid": 1 if sopra_pad else -1}

    def send_command_without_return(self, comando):
        self.send_command_with_return(comando)

    def send_command_with_return(self, comando, timeout=7):
        self._integra_rc()
        self.comandi.append(comando)
        if not comando.endswith("?"):                        # le domande (EXT tof?, battery?) non consumano
            self.batteria -= 0.15
        parti = comando.split()
        nome, valori = parti[0], [float(v) for v in parti[1:] if re.fullmatch(r"-?\d+(\.\d+)?", v)]
        if comando in ("command", "mon", "streamon", "streamoff") or nome in ("speed", "mdirection", "setresolution", "setfps", "setbitrate"):
            return "ok"
        if comando == "sdk?":
            return "30"
        if comando == "sn?":
            return "SIMULATO0001"
        if comando == "hardware?":
            return "RMTT"
        if comando == "battery?":
            return str(int(self.batteria))
        if comando == "EXT tof?":
            return f"tof {self._tof_mm()}"
        if nome == "downvision":
            self.bassa = valori[0] == 1
            return "ok"
        if comando == "takeoff":
            self.in_volo, self.quota = True, 90.0
            time.sleep(2 * FATTORE_TEMPO)
            return "ok"
        if comando == "land":
            self.in_volo, self.quota = False, 0.0
            return "ok"
        if not self.in_volo:
            return "error Not joystick"
        if nome in ("cw", "ccw"):
            gradi = valori[0] * (1 + self._caso.gauss(0, self.rumore))
            self.yaw += gradi if nome == "cw" else -gradi
            self.dir = (self.dir - gradi if nome == "cw" else self.dir + gradi) % 360
            time.sleep(valori[0] / 90 * FATTORE_TEMPO)
            return "ok"
        if nome in ("up", "down"):
            self.quota = max(20.0, self.quota + (valori[0] if nome == "up" else -valori[0]))
            return "ok"
        if nome == "forward":
            return self._muovi(valori[0] / 100 * (1 + self._caso.gauss(0, self.rumore)), 0)
        if nome == "back":
            return self._muovi(valori[0] / 100, 180)
        if nome == "go" and parti[-1].startswith("m"):     # go x y z velocità mN: sopra il pad
            if self.get_current_state()["mid"] == -1 and math.dist((self.x, self.y), self.base[:2]) > 0.6:
                return "error No mission pad"
            self.x, self.y = self.base[:2]
            self.quota = valori[2]
            return "ok"
        return "error unknown command"

    # --- fisica semplificata ---
    def _libero(self, x, y):
        r, c = self.mappa.cella(x, y)
        if not self.mappa.dentro(r, c) or self.mappa.occupata[r, c]:
            return False
        return all(math.dist((x, y), (ox, oy)) > raggio for ox, oy, raggio in self.ostacoli)

    def _muovi(self, metri, angolo_relativo, attesa=True):
        direzione = math.radians(self.dir + angolo_relativo)
        durata = metri / 0.5 * FATTORE_TEMPO if attesa else 0
        passi = max(1, int(metri / 0.02))
        for _ in range(passi):
            nx = self.x + metri / passi * math.cos(direzione)
            ny = self.y + metri / passi * math.sin(direzione)
            if not self._libero(nx, ny):
                self.urti.append((round(nx, 2), round(ny, 2)))
                return "error Collisione"
            self.x, self.y = nx, ny
        if durata > 0:                                       # nei passi classici lo stato mostra la velocità
            self._v_passo = metri * 100 / durata
            time.sleep(durata)
            self._v_passo = 0.0
        return "ok"

    def _tof_mm(self):
        direzione = math.radians(self.dir)
        for i in range(1, 121):
            d = i / 100
            if not self._libero(self.x + d * math.cos(direzione), self.y + d * math.sin(direzione)):
                return int(d * 1000)
        return 8191
