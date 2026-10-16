"""Compatibility seam: private project workflows are not shipped or opened."""
import tkinter as tk
class ProjectPage:
    def __init__(self,app,parent):
        self.app=app;self.bridge=None;self.bridge_error=None
        self.tab=tk.Frame(parent);app.project_tab=self.tab
        self.reply_text=tk.Text(self.tab)
        self.notice=tk.StringVar(value='本公开版未包含视频项目流程。')
    def start_bridge(self):pass
    def close(self):pass
    def refresh(self,*args,**kwargs):pass
    def save_draft(self,*args,**kwargs):pass
    def begin(self,*args,**kwargs):
        self.app.status.set('本公开版未包含视频项目流程。')
    send=begin
    confirm=begin
