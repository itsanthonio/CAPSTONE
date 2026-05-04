
from django.db import models

class Alert(models.Model):
	STATUS_CHOICES = [
		('new', 'New'),
		('investigating', 'Investigating'),
		('resolved', 'Resolved'),
	]
	RISK_LEVEL_CHOICES = [
		('low', 'Low'),
		('moderate', 'Moderate'),
		('high', 'High'),
	]
	alert_id = models.CharField(max_length=20, unique=True)
	timestamp = models.DateTimeField()
	location = models.CharField(max_length=255)
	confidence = models.FloatField()
	status = models.CharField(max_length=20, choices=STATUS_CHOICES)
	type = models.CharField(max_length=100)
	risk_level = models.CharField(max_length=20, choices=RISK_LEVEL_CHOICES)

	def __str__(self):
		return f"{self.alert_id} - {self.type} ({self.status})"
