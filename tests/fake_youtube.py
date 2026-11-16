class FakeYouTube:
    def __init__(self):
        self.uploads, self.fail = [], False

    def upload(self, *, path, title, description, tags, synthetic):
        if self.fail:
            raise RuntimeError("quota exceeded")
        n = len(self.uploads) + 1
        self.uploads.append({"path": path, "title": title, "description": description, "tags": tags, "synthetic": synthetic})
        return {"id": f"vid{n}", "url": f"https://youtube.com/shorts/vid{n}"}
