class JobQueue:
    def claim_job(self):
        return "lease"

    def complete_job(self):
        return "done"
