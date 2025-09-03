# LRZ-Ready Pfade

## A) LRZ Compute Cloud / VM (Docker erlaubt)
1. VM (Ubuntu 22.04), SSH einrichten.
2. `inventories/lrz.ini` füllen (Host, User, Key).
3. Von lokal:
   ansible-playbook -i inventories/lrz.ini playbooks/01_bootstrap.yml
   ansible-playbook -i inventories/lrz.ini playbooks/02_deploy.yml
   ansible-playbook -i inventories/lrz.ini playbooks/03_run_experiment.yml -e crash_fraction=0.3
   ansible-playbook -i inventories/lrz.ini playbooks/04_collect.yml

## B) LRZ HPC (Docker verboten → Apptainer/Singularity + Slurm)
- Vorlage: `lrz/slurm_apptainer.sbatch`
- Alles in einem Node laufen lassen (Apptainer zieht docker:// Images). Netzimpairments mit `tc` erfordern Caps/--fakeroot und sind evtl. eingeschränkt; sonst Fokus auf Crash/Recovery-Sweeps.
- Viele Varianten via Slurm Job Arrays (ENV-Variablen CRASH_FRACTION, LOSS_PCT, LAT_MS, RECOVERY_MODE, NODE_COUNT, TX_RATE).
