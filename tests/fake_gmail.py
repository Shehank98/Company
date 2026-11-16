class FakeGmail:
    """In-memory Gmail. forbid_send makes any send() blow up, to prove read-only paths never send."""
    def __init__(self):
        self.inbox, self.sent, self.drafts, self.labels = [], [], [], []
        self.fail_send = False
        self.forbid_send = False

    def add_mail(self, *, id, frm, subject, body, thread="th-in-1", attachments=()):
        self.inbox.append({"id": id, "thread_id": thread, "rfc_message_id": f"<{id}@mail>", "from_addr": frm.lower(),
                           "subject": subject, "body": body, "attachments": list(attachments), "labels": ["INBOX"]})

    def list_inbox(self, max_results=25):
        return [{"id": m["id"], "threadId": m["thread_id"]} for m in self.inbox]

    def get_message(self, msg_id):
        return next(m for m in self.inbox if m["id"] == msg_id)

    def create_draft(self, *, to, subject, body, thread_id):
        self.drafts.append({"to": to, "subject": subject, "body": body, "thread_id": thread_id})
        return f"draft-{len(self.drafts)}"

    def send(self, *, to, subject, body, attachments, thread_id, in_reply_to):
        assert not self.forbid_send, "send() must never be called on this path"
        if self.fail_send:
            raise RuntimeError("gmail down")
        n = len(self.sent) + 1
        self.sent.append({"to": to, "subject": subject, "body": body, "attachments": attachments,
                          "thread_id": thread_id, "in_reply_to": in_reply_to})
        return {"id": f"sent-{n}", "threadId": thread_id or f"th-out-{n}", "rfc_message_id": f"<sent-{n}@fake>"}

    def add_label(self, msg_id, label):
        self.labels.append((msg_id, label))
