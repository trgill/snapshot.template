import errno
import os
import subprocess
import logging

class LvmBug(RuntimeError):
	"""
	Things that are clearly a bug with lvm itself.
	"""
	def __init__(self, msg):
		super().__init__(msg)

	def __str__(self):
		return "lvm bug encountered: %s" % ' '.join(self.args)

class SnapshotStatus():
    SNAPSHOT_OK = 0
    ERROR_INSUFFICENT_SPACE = 1
    ERROR_ALREADY_DONE = 2
    ERROR_SNAPSHOT_FAILED = 3
    ERROR_REMOVE_FAILED = 4
    ERROR_REMOVE_FAILED_NOT_SNAPSHOT = 5
    ERROR_LVS_FAILED = 6
    ERROR_NAME_TOO_LONG = 7
    ERROR_ALREADY_EXISTS = 8
    ERROR_NAME_CONFLICT = 8
    
# what percentage is part of whole
def percentage(part, whole):
  return 100 * float(part)/float(whole)

# what is number is percent of whole
def percentof(percent, whole):
  return float(whole) / 100 * float(percent)

def run_command(argv, stdin=None, env_prune=None, stderr_to_stdout=False, binary_output=False):
    if env_prune is None:
        env_prune = []

    env = os.environ.copy()
    env.update({"LC_ALL": "C"})

    for var in env_prune:
        env.pop(var, None)
 
    if stderr_to_stdout:
        stderr_dir = subprocess.STDOUT
    else:
        stderr_dir = subprocess.PIPE
    try:
        proc = subprocess.Popen(argv,
                                stdin=stdin,
                                stdout=subprocess.PIPE,
                                stderr=stderr_dir,
                                close_fds=True,
                                env=env)

        out, err = proc.communicate()
    
        out = out.decode("utf-8")

        if out:
            if not stderr_to_stdout:
                logging.info("stdout:")
            for line in out.splitlines():
                logging.info("%s", line)

        if not stderr_to_stdout and err:
            logging.info("stderr:")
            for line in err.splitlines():
                logging.info("%s", line)

    except OSError as e:
        print("Error running %s: %s", argv[0], e.strerror)
        raise

    return (proc.returncode, out)

def check_positive(value):
    try:
        value = int(value)
        if value <= 0:
            raise argparse.ArgumentTypeError("{} is not a positive integer".format(value))
    except ValueError:
        raise Exception("{} is not an integer".format(value))
    return value
