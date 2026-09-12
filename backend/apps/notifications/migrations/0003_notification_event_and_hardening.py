import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0002_alter_notificationoutbox_channel_and_more'),
        ('tenancy', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # Milestone 18.1 #14 -- remove non-functional quiet-hours setup fields
        migrations.RemoveField(model_name='communicationsetup', name='quiet_hours_enabled'),
        migrations.RemoveField(model_name='communicationsetup', name='quiet_hours_start'),
        migrations.RemoveField(model_name='communicationsetup', name='quiet_hours_end'),

        # #7 -- guardian recipient selection becomes rule-level policy
        migrations.AddField(
            model_name='notificationrule',
            name='recipient_policy',
            field=models.CharField(
                blank=True, max_length=30,
                choices=[('PRIMARY', 'Primary only'), ('PRIMARY_AND_EMERGENCY', 'Primary and emergency contacts')],
            ),
        ),

        # #1 -- durable NotificationEvent transactional boundary
        migrations.CreateModel(
            name='NotificationEvent',
            fields=[
                ('status', models.CharField(choices=[('PENDING', 'Pending'), ('PROCESSING', 'Processing'), ('PROCESSED', 'Processed'), ('FAILED', 'Failed')], default='PENDING', max_length=20)),
                ('attempts', models.PositiveSmallIntegerField(default=0)),
                ('last_error', models.TextField(blank=True, default='')),
                ('available_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('lease_expires_at', models.DateTimeField(blank=True, null=True)),
                ('processed_at', models.DateTimeField(blank=True, null=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('event_code', models.CharField(max_length=80)),
                ('dedupe_key', models.CharField(max_length=160)),
                ('context', models.JSONField(default=dict)),
                ('recipient_refs', models.JSONField(default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='%(class)ss', to='tenancy.tenant')),
            ],
            options={
                'indexes': [models.Index(fields=['tenant', 'status', 'available_at'], name='notificatio_tenant__f2fb3d_idx')],
                'constraints': [models.UniqueConstraint(fields=('tenant', 'dedupe_key'), name='unique_notification_event_dedupe_per_tenant')],
            },
        ),

        # #2 -- structured IN_APP recipient instead of overloading `recipient`
        migrations.AlterField(
            model_name='notificationoutbox',
            name='recipient',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name='notificationoutbox',
            name='recipient_user',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddConstraint(
            model_name='notificationoutbox',
            constraint=models.CheckConstraint(
                condition=Q(
                    Q(('channel', 'IN_APP'), ('recipient', ''), ('recipient_user__isnull', False)),
                    Q(Q(('channel', 'IN_APP'), _negated=True), ('recipient_user__isnull', True)),
                    _connector='OR',
                ),
                name='in_app_uses_recipient_user_others_use_recipient',
            ),
        ),

        # #9 -- delivery attempt bookkeeping invariant
        migrations.AddConstraint(
            model_name='notificationdeliveryattempt',
            constraint=models.UniqueConstraint(fields=('notification', 'attempt_number'), name='unique_delivery_attempt_number'),
        ),

        # #10 -- IN_APP delivery idempotent across a worker crash
        migrations.AddField(
            model_name='usernotification',
            name='source_notification',
            field=models.OneToOneField(default=None, on_delete=django.db.models.deletion.PROTECT, related_name='user_notification', to='notifications.notificationoutbox'),
            preserve_default=False,
        ),
    ]
