from dataclasses import dataclass
import struct
import time
import math
import threading

from . import telemetry

ENDIANNESS = "<"
POLLING_RATE = 50

@dataclass
class Pid:
	name: str
	idx: int
	kp: float = 0
	ki: float = 0
	kd: float = 0

	def set(self, kp, ki, kd):
		self.kp = kp
		self.ki = ki
		self.kd = kd
		return self

	def from_bytes(self, bys):
		kp, ki, kd = struct.unpack(ENDIANNESS + "fff", bys)
		self.set(kp, ki, kd)
		return self

	def to_bytes(self):
		return struct.pack(ENDIANNESS + "fff", self.kp, self.ki, self.kd)

# Base class with I2C comm helpers

class I2CBase:
	def __init__(self, bus=None, addr=None):
		self.bus = bus
		self.addr = addr
		self.i2c_simulate = bus is None
		self.lock = threading.Lock()

	def write(self, reg, data):
		if self.i2c_simulate:
			return

		with self.lock:
			self.bus.write_i2c_block_data(self.addr, reg, data)

	def read(self, reg, size):
		if self.i2c_simulate:
			return b"\x00"*size

		with self.lock:
			dat = bytes(self.bus.read_i2c_block_data(self.addr, reg, size))

		return dat

	def write_cmd(self, reg):
		self.write(reg, [])

	def write_struct(self, reg, fmt, *data):
		self.write(reg, struct.pack(ENDIANNESS + fmt, *data))

	def read_struct(self, reg, fmt):
		ret = self.read(reg, struct.calcsize(fmt))
		return struct.unpack(ENDIANNESS + fmt, ret)

# A decorator to block until the command has finished
def block_cmd(stoppable=False):

	def decorator(func):

		def inner(self, *args, **kwargs):
			# Get default blocking behaviour
			blocking = self.is_blocking()

			# Check if we got asked to force blocking or not, replace default
			if "blocking" in kwargs:
				blocking = kwargs["blocking"]
				del kwargs["blocking"]

			# If this is a new stoppable command, wait until we're back running
			if stoppable:
				self.running_flag.wait()

			# Call the function normally
			func(self, *args, **kwargs)

			# If we're simulating, don't block at all
			if self.i2c_simulate:
				return

			# If we need to block, do so
			if blocking:
				self.wait_completed()

			# After an emergency stop, we've reached target so the blocking finishes
			# We still want to block until we can start running again.
			if stoppable:
				self.running_flag.wait()

		return inner

	return decorator


# Base class for pico microcontrollers on robots

class PicoBase(I2CBase):
	def __init__(self, bus=None, addr=None):
		super().__init__(bus, addr)
		self.set_blocking(True)
		self.set_running(False)
		self.telems = {}
		# The running flag is set if we can execute stoppable commands
		self.running_flag = threading.Event()
		self.running_flag.set()

	# Data helpers

	def telem_from_name(self, name):
		for telem in self.telems.values():
			if telem.name == name:
				return telem
		return None

	def telem_from_idx(self, idx):
		if idx in self.telems:
			return self.telems[idx]
		return None

	# Blocking state logic

	def set_blocking(self, val):
		self.blocking = val

	def is_blocking(self):
		return self.blocking

	# Stoppable state logic

	def notify_stop(self):
		if self.running_flag.is_set():
			self.notify_stop_action()
		self.running_flag.clear()

	def notify_stop_clear(self):
		self.running_flag.set()

	# To be overriden by classes extending this one
	def notify_stop_action(self):
		pass

	# Writer registers/ orders

	@block_cmd()
	def set_running(self, state):
		state = not not state
		self.write_struct(0, "B", state)
		self.running = state

	def start(self):
		self.set_running(True)

	def stop(self):
		self.set_running(False)

	def set_telem(self, telem, state):
		self.write_struct(6 | (telem.idx << 4), "?", state)

	# Read registers

	def ready_for_order(self):
		return self.read_struct(10, "?")[0] == 1

	# Command Helpers

	def wait_completed(self):
		while not self.ready_for_order():
			time.sleep(1.0/POLLING_RATE)

# Class for the pico that handles moving

class Asserv(PicoBase):
	def __init__(self, bus=None, addr=None):
		super().__init__(bus, addr)

		self.last_pos = (0,0) # rho, theta
		self.last_pos_xy = (0,0) # x, y
		self.set_running(False)
		self.pids = {}
		for pid in [Pid("theta", 0), Pid("rho", 1), Pid("left_vel", 2), Pid("right_vel", 3)]:
			self.get_pid(pid)
			idx = pid.idx
			self.pids[idx] = pid
			self.telems[idx] = telemetry.Telemetry(f"pid_{pid.name}", idx, telemetry.PidTelemetryPacket)

		for telem in self.telems.values():
			self.set_telem(telem, False)

	# Data helpers

	def pid_from_name(self, name):
		for pid in self.pids.values():
			if pid.name == name:
				return pid
		return None

	def pid_from_idx(self, idx):
		if idx in self.pids:
			return self.pids[idx]
		return None

	# Stoppable state logic

	def notify_stop_action(self):
		self.emergency_stop()

	# Write registers/ orders

	@block_cmd(stoppable=True)
	def move(self, rho, theta):
		self.write_struct(1, "ff", rho, theta)

	def move_abs(self, tx, ty):
		dst, theta = self.get_pos()
		cx, cy = self.get_pos_xy()
		dx = tx - cx
		dy = ty - cy

		deltaTheta = (math.atan2(dy, dx)-theta)
		deltaDst = math.sqrt(dx * dx + dy * dy)

		sign = 1 if deltaTheta > 0 else -1

		deltaTheta %= sign*2*math.pi

		if abs(deltaTheta) > math.pi:
			deltaTheta = deltaTheta - sign*2*math.pi

		#print(f"Moving {deltaTheta}rads, {deltaDst}mm")
		self.move(deltaDst, deltaTheta)

	def emergency_stop(self):
		self.write_cmd(0 | (1 << 4))

	def set_pid(self, pid):
		self.pids[pid.idx] = pid
		self.write(5 | (pid.idx << 4), pid.to_bytes())

	def set_dst_speedprofile(self, vmax, amax):
		return self.write_struct(13 | (0 << 4), "ff", vmax, amax)

	def set_angle_speedprofile(self, vmax, amax):
		return self.write_struct(13 | (1 << 4), "ff", vmax, amax)

	# Read registers

	def get_pos(self):
		self.last_pos = self.read_struct(3 | (0 << 4), "ff")
		return self.last_pos

	def get_pos_xy(self):
		self.last_pos_xy = self.read_struct(3 | (1 << 4), "ff")
		return self.last_pos_xy

	def get_pid(self, pid):
		ret = self.read(2 | (pid.idx << 4), 4*3)
		return pid.from_bytes(ret)

	def get_dst_speedprofile(self):
		return self.read_struct(12 | (0 << 4), "ff")

	def get_angle_speedprofile(self):
		return self.read_struct(12 | (1 << 4), "ff")

	# Read/Write

	# returns left,right ticks
	def debug_get_encoders(self):
		return self.read_struct(11 | (0 << 4), "ii")

	# sets motor speed values manually
	def debug_set_motors(self, left, right):
		self.write_struct(11 | (1 << 4), "ff", left, right)

	def debug_set_target(self, dst, theta):
		self.write_struct(11 | (2 << 4), "ff", dst, theta)

	def debug_set_motors_enable(self, state):
		state = not not state
		self.write_struct(11 | (3 << 4), "?", state)

	def debug_get_controller_state(self):
		return self.read_struct(11 | (4 << 4), "B")[0]

# Class for the pico that handles actuators

class Action(PicoBase):
	def __init__(self, bus=None, addr=None):
		super().__init__(bus, addr)

	# Read

	def elev_homed(self):
		return self.read_struct(1 | (3 << 4), "?")[0]

	def elev_pos(self):
		return self.read_struct(1 | (4 << 4), "f")[0]

	def right_arm_deployed(self):
		return self.read_struct(2 | (3 << 4), "?")[0]

	def right_arm_angles(self):
		return self.read_struct(2 | (4 << 4), "ff")

	def left_arm_deployed(self):
		return self.read_struct(3 | (3 << 4), "?")[0]

	def left_arm_angles(self):
		return self.read_struct(3 | (4 << 4), "ff")

	# Write

	@block_cmd()
	def elev_home(self):
		self.write_cmd(1 | (0 << 4))

	@block_cmd()
	def elev_move_abs(self, pos):
		self.write_struct(1 | (1 << 4), "f", pos)

	@block_cmd()
	def elev_move_rel(self, pos):
		self.write_struct(1 | (2 << 4), "f", pos)

	@block_cmd()
	def right_arm_deploy(self):
		self.write_cmd(2 | (0 << 4))

	@block_cmd()
	def right_arm_fold(self):
		self.write_cmd(2 | (1 << 4))

	@block_cmd()
	def right_arm_turn(self, angle):
		self.write_struct(2 | (2 << 4), "f", angle)

	@block_cmd()
	def left_arm_deploy(self):
		self.write_cmd(3 | (0 << 4))

	@block_cmd()
	def left_arm_fold(self):
		self.write_cmd(3 | (1 << 4))

	@block_cmd()
	def left_arm_turn(self, angle):
		self.write_struct(3 | (2 << 4), "f", angle)

	@block_cmd()
	def pump_enable(self, pump_idx, state):
		self.write_struct(4 | (pump_idx << 4), "?", state)