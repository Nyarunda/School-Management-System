from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers, status
from rest_framework.authtoken.models import Token
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .services import accept_invite


class LoginView(ObtainAuthToken):
    """Reuses DRF's built-in username/password -> token view as-is (returns
    {"token": "..."}) rather than reinventing it -- deliberately keeps DRF's
    native error shape instead of this project's usual api_validation_error
    convention, since this is a stock, well-tested view, not our
    service-layer pattern.
    """

    permission_classes = [AllowAny]


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class InviteAcceptSerializer(serializers.Serializer):
    token = serializers.CharField()
    password = serializers.CharField()


class InviteAcceptView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = InviteAcceptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            user = accept_invite(token=serializer.validated_data["token"], password=serializer.validated_data["password"])
        except DjangoValidationError as error:
            return Response({"detail": error.messages}, status=status.HTTP_400_BAD_REQUEST)
        token, _ = Token.objects.get_or_create(user=user)
        return Response({"token": token.key}, status=status.HTTP_200_OK)
