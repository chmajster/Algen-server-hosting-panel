from __future__ import annotations

from pathlib import Path

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required

from panel.forms.files import CreateFolderForm, RenamePathForm, TextFileForm, UploadForm
from panel.services.audit import log_activity
from panel.services.files import (
    FileManagerError,
    client_root,
    copy_path,
    create_folder,
    delete_path,
    list_directory,
    move_path,
    save_upload,
    safe_join,
    write_text_file,
)
from panel.services.resource_limits import (
    estimate_client_live_disk_mb,
    estimate_client_live_inode_count,
    estimate_upload_size,
    resource_usage_report,
)
from panel.utils.decorators import active_account_required, roles_required
from panel.utils.query import current_client


files_bp = Blueprint("files", __name__)


def _enforce_storage_growth_limits(client, *, delta_bytes: int = 0, delta_inodes: int = 0) -> None:
    report = resource_usage_report(client)
    disk_metric = report.get("disk_mb", {})
    inode_metric = report.get("inode_count", {})

    hard_disk = disk_metric.get("hard_limit")
    if hard_disk is not None:
        current_disk = estimate_client_live_disk_mb(client)
        projected_disk = current_disk + (max(0, delta_bytes) / (1024 * 1024))
        if projected_disk >= hard_disk:
            raise FileManagerError(
                f"Przekroczono twardy limit dysku ({round(projected_disk, 2)} MB / {hard_disk} MB)."
            )

    hard_inode = inode_metric.get("hard_limit")
    if hard_inode is not None:
        current_inodes = estimate_client_live_inode_count(client)
        projected_inodes = current_inodes + max(0, delta_inodes)
        if projected_inodes >= hard_inode:
            raise FileManagerError(
                f"Przekroczono twardy limit inodow ({projected_inodes} / {int(hard_inode)})."
            )


@files_bp.route("/client/files", methods=["GET", "POST"])
@login_required
@roles_required("client")
@active_account_required
def manager():
    client = current_client()
    root = client_root(client.id)
    current_path = request.args.get("path", "")
    upload_form = UploadForm()
    folder_form = CreateFolderForm()
    rename_form = RenamePathForm()
    text_form = TextFileForm()
    upload_form.target_dir.data = current_path

    try:
        items = list_directory(root, current_path)
    except FileManagerError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("files.manager"))

    return render_template(
        "files/manager.html",
        items=items,
        current_path=current_path,
        upload_form=upload_form,
        folder_form=folder_form,
        rename_form=rename_form,
        text_form=text_form,
    )


@files_bp.route("/client/files/upload", methods=["POST"])
@login_required
@roles_required("client")
@active_account_required
def upload():
    client = current_client()
    form = UploadForm()
    if form.validate_on_submit():
        root = client_root(client.id)
        try:
            upload_size = estimate_upload_size(form.file.data)
            _enforce_storage_growth_limits(client, delta_bytes=upload_size, delta_inodes=1)
            save_upload(root, form.target_dir.data or "", form.file.data)
            log_activity("files.upload", "file", f"Wysłano plik do {form.target_dir.data or '/'}", client=client)
            flash("Plik został wysłany.", "success")
        except FileManagerError as exc:
            flash(str(exc), "danger")
    return redirect(url_for("files.manager", path=form.target_dir.data or ""))


@files_bp.route("/client/files/folder", methods=["POST"])
@login_required
@roles_required("client")
@active_account_required
def folder():
    client = current_client()
    form = CreateFolderForm()
    if form.validate_on_submit():
        root = client_root(client.id)
        try:
            _enforce_storage_growth_limits(client, delta_bytes=0, delta_inodes=1)
            create_folder(root, form.path.data)
            log_activity("files.mkdir", "directory", f"Utworzono katalog {form.path.data}", client=client)
            flash("Folder został utworzony.", "success")
        except FileManagerError as exc:
            flash(str(exc), "danger")
    return redirect(url_for("files.manager"))


@files_bp.route("/client/files/save", methods=["POST"])
@login_required
@roles_required("client")
@active_account_required
def save_text():
    client = current_client()
    form = TextFileForm()
    if form.validate_on_submit():
        root = client_root(client.id)
        try:
            target = safe_join(root, form.path.data)
            previous_size = target.stat().st_size if target.exists() and target.is_file() else 0
            new_size = len((form.content.data or "").encode("utf-8"))
            delta_bytes = max(0, new_size - previous_size)
            delta_inodes = 0 if target.exists() else 1
            _enforce_storage_growth_limits(client, delta_bytes=delta_bytes, delta_inodes=delta_inodes)
            write_text_file(root, form.path.data, form.content.data)
            log_activity("files.save_text", "file", f"Zapisano plik {form.path.data}", client=client)
            flash("Plik został zapisany.", "success")
        except FileManagerError as exc:
            flash(str(exc), "danger")
    return redirect(url_for("files.manager"))


@files_bp.route("/client/files/rename", methods=["POST"])
@login_required
@roles_required("client")
@active_account_required
def rename():
    client = current_client()
    form = RenamePathForm()
    if form.validate_on_submit():
        root = client_root(client.id)
        try:
            move_path(root, form.source.data, form.destination.data)
            log_activity("files.rename", "file", f"Przeniesiono {form.source.data} do {form.destination.data}", client=client)
            flash("Ścieżka została zmieniona.", "success")
        except FileManagerError as exc:
            flash(str(exc), "danger")
    return redirect(url_for("files.manager"))


@files_bp.route("/client/files/delete", methods=["POST"])
@login_required
@roles_required("client")
@active_account_required
def delete():
    client = current_client()
    target = request.form.get("path", "")
    root = client_root(client.id)
    try:
        delete_path(root, target)
        log_activity("files.delete", "file", f"Usunięto {target}", client=client)
        flash("Element został usunięty.", "warning")
    except FileManagerError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("files.manager"))
