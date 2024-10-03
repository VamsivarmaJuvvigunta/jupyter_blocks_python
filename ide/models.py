from django.db import models
from django.contrib.auth.models import User
import json

class CodeExecutionSession(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True)
    session_id = models.CharField(max_length=255)
    variables = models.TextField(default="{}")  
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def get_variables(self):
        return json.loads(self.variables)

    def set_variables(self, variables_dict):
        self.variables = json.dumps(variables_dict)
        self.save()
