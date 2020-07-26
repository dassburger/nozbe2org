#!/usr/bin/python3
import datetime
import json
import logging
import os
import sys
import urllib.request
from collections import namedtuple

from PyOrgMode import PyOrgMode

TASK_INDENT = "   "

class Nozbe:
    Project = namedtuple("Project", ["id", "name", "description", "tasks"])
    Task = namedtuple("Task", ["id", "project", "name", "completed", "datetime", "comments", "contexts"])
    Comment = namedtuple("Comment", ["id", "type", "created_at", "task", "body", "uploads"])
    Upload = namedtuple("Upload", ["id", "comment", "name", "url"])

    DETACHED_COMMENT = Comment("detached", "detached", None, None, None, [])

    projects_by_id = {}
    projects_by_name = {}
    tasks_by_id = {}
    comments_by_id = {}
    uploads_by_id = {}

    def __init__(self, nozbe_data):
        self.projects_by_id = {}
        self.projects_by_name = {}
        self.load_projects(nozbe_data["project"])
        self.load_tasks(nozbe_data["task"])
        self.load_uploads(nozbe_data["upload"])

    def load_projects(self, nozbe_projects):
        logging.info("Loading %d Nozbe projects", len(nozbe_projects))
        for p in nozbe_projects:
            logging.info("Loading project %s (%s)", p["name"], p["id"])
            p = Nozbe.Project(p["id"], p["name"], p["description"], [])
            self.projects_by_id[p.id] = p
            self.projects_by_name[p.name] = p

    def load_tasks(self, nozbe_tasks):
        logging.info("Loading %d Nozbe tasks", len(nozbe_tasks))
        for t in nozbe_tasks:
            logging.info("Loading task %s (%s) project %s (%s)", t["name"], t["id"], t["_project_name"],
                         t["project_id"])
            project = self.projects_by_id[t["project_id"]]
            contexts = t["_con_names"] if t["_con_names"] else []
            task = Nozbe.Task(t["id"], project, t["name"], t["completed"], t["datetime"], [], contexts)
            self.tasks_by_id[task.id] = task
            self.load_comments(task, t["comments"])
            project.tasks.append(task)

    def load_comments(self, task, nozbe_comments):
        logging.info("Loading task's %d comments", len(nozbe_comments))
        for c in nozbe_comments:
            if c["deleted"]:
                logging.info("Skipping deleted comment %s", c["id"])
                return
            comment = Nozbe.Comment(c["id"], c["type"], c["_created_at"], task, c["body"], [])
            task.comments.append(comment)
            self.comments_by_id[comment.id] = comment

    def load_uploads(self, nozbe_uploads):
        logging.info("Loading %d Nozbe uploads", len(nozbe_uploads))
        for u in nozbe_uploads:
            comment = self.comments_by_id.setdefault(u["comment_id"], self.DETACHED_COMMENT)
            upload = Nozbe.Upload(u["id"], comment, u["name"], u["_url"])
            self.uploads_by_id[upload.id] = upload
            comment.uploads.append(upload)


def convert_project(nozbe_project: Nozbe.Project):
    logging.info("Converting Nozbe project %s (%s) to Org", nozbe_project.name, nozbe_project.id)
    org_project = PyOrgMode.OrgDataStructure()
    if nozbe_project.description:
        org_project.root.append_clean(nozbe_project.description + "\n")
    tasks_root = PyOrgMode.OrgNode.Element()
    tasks_root.heading = "Tasks"
    org_project.root.append_clean(tasks_root)

    for t in nozbe_project.tasks:
        tasks_root.append_clean(convert_task(t))

    org_project.save_to_file(nozbe_project.name + ".org")


def convert_task(nozbe_task: Nozbe.Task):
    logging.info("Adding task %s of project %s", nozbe_task.name, nozbe_task.project.name)
    item = PyOrgMode.OrgNode.Element()
    item.heading = nozbe_task.name
    if nozbe_task.contexts:
        item.heading += " "  # bug in PyOrgMode
        item.tags = convert_contexts(nozbe_task)
    item.todo = "TODO" if not nozbe_task.completed else "DONE"
    if nozbe_task.datetime:
        scheduled = PyOrgMode.OrgSchedule.Element(scheduled=convert_nozbe_datetime(nozbe_task.datetime))
        scheduled.indent = "   "
        item.append_clean(scheduled)
    if nozbe_task.comments:
        nozbe_task.comments.reverse()
        for c in nozbe_task.comments:
            comment = convert_comment(c)
            if comment:
                item.append_clean(comment)
    return item


def convert_comment(nozbe_comment: Nozbe.Comment):
    logging.info("Converting comment %s of task %s (%s)", nozbe_comment.id, nozbe_comment.task.name,
                 nozbe_comment.task.id)
    if nozbe_comment.type == "markdown":
        logging.info("Adding %s comment" % nozbe_comment.type)
        return convert_nozbe_markdown(nozbe_comment.body)
    elif nozbe_comment.type == "checklist":
        logging.info("Adding %s comment" % nozbe_comment.type)
        return convert_nozbe_checklist(nozbe_comment.body)
    elif nozbe_comment.type == "file":
        logging.info("Adding %s comment" % nozbe_comment.type)
        if not nozbe_comment.uploads:
            logging.warning("No uploads found for comment %s of type %s", nozbe_comment.id, nozbe_comment.type)
            return
        download_file(nozbe_comment.uploads[0])
        return convert_nozbe_file(nozbe_comment.uploads[0])
    else:
        logging.warning("Skipping comment of unsupported type %s", nozbe_comment.type)


def convert_nozbe_checklist(body):
    return body.replace("(-)", f"{TASK_INDENT}- [ ]").replace("(+)", f"{TASK_INDENT}- [X]") + "\n"


def convert_nozbe_markdown(body: str):
    return TASK_INDENT + body.replace("\n", f"\n{TASK_INDENT}") + "\n"


def convert_nozbe_file(attachment):
    return "   [[" + attachment_file_name(attachment) + "]]\n"


def attachment_file_name(nozbe_upload: Nozbe.Upload):
    name, ext = os.path.splitext(nozbe_upload.name)
    newname = "./%s-%s%s" % (name, nozbe_upload.comment.id, ext)
    return newname


def download_file(attachment):
    local_location = attachment_file_name(attachment)
    logging.info("Downloading %s to %s", attachment.name, local_location)
    response = urllib.request.urlopen(attachment.url)
    with open(local_location, "wb") as output:
        output.write(response.read())


def convert_nozbe_datetime(dt):
    ts = datetime.datetime.strptime(dt, "%Y-%m-%d %H:%M:%S")
    return ts.strftime("<%Y-%m-%d %a>")


def convert_contexts(nozbe_task):
    return [c.replace("@", "").lower() for c in nozbe_task.contexts]


def read_whole_file(file_name):
    logging.info("Opening file %s", file_name)
    with open(file_name, 'r') as nozbe_file:
        return nozbe_file.read()


def main(argv):
    decoder = json.decoder.JSONDecoder()
    file_content = read_whole_file(argv[1])
    logging.info("JSON-decoding %d bytes of data", len(file_content))
    nozbe_data = decoder.decode(file_content)
    nozbe = Nozbe(nozbe_data)
    for nozbe_project in nozbe.projects_by_id.values():
        convert_project(nozbe_project)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main(sys.argv)