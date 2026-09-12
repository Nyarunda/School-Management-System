# Risk: HIGH (finance callback/request tables). See docs/architecture/mpesa-endpoints.md.
# Additive schema; database defaults keep old INSERTs valid during expansion.
# Ordinary index/constraint creation needs a measured maintenance window on large
# tables. Route all gateway traffic to the new version at cutover; old callback
# workers must not bypass inbox verification. Forward-fix after new requests exist.

import apps.finance.fields
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('finance', '0007_mpesacallbacklog_mpesastkpushrequest_and_more'),
        ('students', '0002_student_students_st_tenant__b74932_idx_and_more'),
        ('tenancy', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='mpesacallbacklog',
            name='attempts',
            field=models.PositiveIntegerField(db_default=0, default=0),
        ),
        migrations.AddField(
            model_name='mpesacallbacklog',
            name='last_error',
            field=models.CharField(blank=True, db_default="", max_length=240),
        ),
        migrations.AddField(
            model_name='mpesacallbacklog',
            name='processed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='mpesacallbacklog',
            name='request_id',
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='mpesacallbacklog',
            name='status',
            field=models.CharField(choices=[('RECEIVED', 'Awaiting verification'), ('PROCESSED', 'Processed'), ('FAILED', 'Processing failed'), ('REJECTED', 'Rejected')], db_default='RECEIVED', default='RECEIVED', max_length=20),
        ),
        migrations.AddField(
            model_name='mpesacallbacklog',
            name='verification_reference',
            field=models.CharField(blank=True, db_default="", max_length=240),
        ),
        migrations.AddField(
            model_name='mpesacallbacklog',
            name='verified_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='mpesacallbacklog',
            name='verified_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='verified_mpesa_callbacks', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='mpesastkpushrequest',
            name='confirmed_receipt',
            field=models.CharField(blank=True, max_length=120, null=True),
        ),
        migrations.AddField(
            model_name='mpesastkpushrequest',
            name='idempotency_key',
            field=models.CharField(blank=True, max_length=120, null=True),
        ),
        migrations.AddField(
            model_name='mpesastkpushrequest',
            name='provider_query',
            field=models.JSONField(db_default={}, default=dict),
        ),
        migrations.AddField(
            model_name='mpesastkpushrequest',
            name='queried_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='mpesastkpushrequest',
            name='checkout_request_id',
            field=models.CharField(blank=True, max_length=60, null=True),
        ),
        migrations.AlterField(
            model_name='mpesastkpushrequest',
            name='merchant_request_id',
            field=models.CharField(blank=True, max_length=60),
        ),
        migrations.AlterField(
            model_name='mpesastkpushrequest',
            name='status',
            field=models.CharField(choices=[('INITIATING', 'Initiating'), ('UNKNOWN', 'Provider outcome unknown'), ('PENDING', 'Pending'), ('COMPLETED', 'Completed'), ('FAILED', 'Failed')], default='PENDING', max_length=20),
        ),
        migrations.AlterField(
            model_name='tenantmpesaconfiguration',
            name='consumer_key',
            field=apps.finance.fields.EncryptedCharField(max_length=1024),
        ),
        migrations.AlterField(
            model_name='tenantmpesaconfiguration',
            name='consumer_secret',
            field=apps.finance.fields.EncryptedCharField(max_length=1024),
        ),
        migrations.AlterField(
            model_name='tenantmpesaconfiguration',
            name='passkey',
            field=apps.finance.fields.EncryptedCharField(max_length=1024),
        ),
        migrations.AddIndex(
            model_name='mpesacallbacklog',
            index=models.Index(fields=['tenant', 'status', 'created_at'], name='finance_mpe_tenant__44855c_idx'),
        ),
        migrations.AddConstraint(
            model_name='mpesastkpushrequest',
            constraint=models.UniqueConstraint(fields=('tenant', 'idempotency_key'), name='unique_stk_idempotency_per_tenant'),
        ),
        migrations.AddConstraint(
            model_name='mpesastkpushrequest',
            constraint=models.UniqueConstraint(fields=('tenant', 'confirmed_receipt'), name='unique_stk_receipt_per_tenant'),
        ),
    ]
