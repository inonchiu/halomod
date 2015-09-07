#===============================================================================
# Some Imports
#===============================================================================
from scipy.interpolate import InterpolatedUnivariateSpline as spline
import scipy.integrate as intg
import numpy as np
from scipy.optimize import minimize
# import scipy.special as sp

from hmf import MassFunction
from hmf._cache import cached_property, parameter
# import hmf.tools as ht
import tools
import hod
from concentration import CMRelation
from fort.routines import hod_routines as fort
from twohalo_wrapper import twohalo_wrapper as thalo
from twohalo_wrapper import dblsimps
# from hmf.filters import TopHat
from copy import deepcopy
from numpy import issubclass_
from hmf._framework import get_model
import profiles
import bias
import astropy.units as u

USEFORT = True
#===============================================================================
# The class itself
#===============================================================================
class HaloModel(MassFunction):
    '''
    Calculates several quantities using the halo model.

    Parameters
    ----------
    r : array_like, optional, default ``np.logspace(-2.0,1.5,100)``
        The scales at which the correlation function is calculated in Mpc/*h*

    **kwargs: anything that can be used in the MassFunction class

    '''
    rlog = True

    def __init__(self, rmin=0.1, rmax=50.0, rnum=20, hod_params={},
                 hod_model="Zehavi05",
                 halo_profile='NFW', cm_relation='Duffy', bias_model="Tinker10",
                 nonlinear=True, scale_dependent_bias=True,
                 halo_exclusion="None", ng=None, nthreads_2halo=0,
                 proj_limit=None, bias_params={}, cm_params={}, ** hmf_kwargs):

        # Do Mass Function __init__ MUST BE DONE FIRST (to init Cache)
        super(HaloModel, self).__init__(**hmf_kwargs)

        # Initially save parameters to the class.
        self.hod_params = hod_params
        self.hod_model = hod_model
        self.halo_profile = halo_profile
        self.cm_relation = cm_relation
        self.bias_model = bias_model
        self.rmin = rmin
        self.rmax = rmax
        self.rnum = rnum
        self.nonlinear = nonlinear
        self.halo_exclusion = halo_exclusion
        self.scale_dependent_bias = scale_dependent_bias
        self.bias_params = bias_params
        self.nthreads_2halo = nthreads_2halo
        self.cm_params = cm_params
        # A special argument, making it possible to define M_min by mean density
        self.ng = ng

        # Find mmin if we want to
        if ng is not None:
            mmin = self._find_m_min(ng)
            self.hod_params = {"M_min":mmin}


    def update(self, **kwargs):
        """
        Updates any parameter passed
        """
        if "ng" in kwargs:
            self.ng = kwargs.pop('ng')
        elif "hod_params" in kwargs:
            if "M_min" in kwargs["hod_params"]:
                self.ng = None

        super(HaloModel, self).update(**kwargs)

        if self.ng is not None:
            mmin = self._find_m_min(self.ng)
            self.hod_params = {"M_min":mmin}

#===============================================================================
# Parameters
#===============================================================================
    @parameter
    def cut_fit(self, val):
        return False

    @parameter
    def ng(self, val):
        """Mean density of galaxies, ONLY if passed directly"""
        return val

    @parameter
    def bias_params(self, val):
        return val

    @parameter
    def hod_params(self, val):
        """Dictionary of parameters for the HOD model"""
        return val

    @parameter
    def hod_model(self, val):
        """:class:`~hod.HOD` class"""
        if not isinstance(val, basestring) and not issubclass_(val, hod.HOD):
            raise ValueError("hod_model must be a subclass of hod.HOD")
        return val



    @parameter
    def nonlinear(self, val):
        """Logical indicating whether the power is to be nonlinear or not"""
        try:
            if val:
                return True
            else:
                return False
        except:
            raise ValueError("nonlinear must be a logical value")

    @parameter
    def halo_profile(self, val):
        """The halo density profile"""
        if not isinstance(val, basestring) and not issubclass_(val, profiles.Profile):
            raise ValueError("halo_profile must be a subclass of profiles.Profile")
        return val

    @parameter
    def cm_relation(self, val):
        """A concentration-mass relation"""
        if not isinstance(val, basestring) and not issubclass_(val, CMRelation):
            raise ValueError("cm_relation must be a subclass of concentration.CMRelation")
        return val

    @parameter
    def bias_model(self, val):
        if not isinstance(val, basestring) and not issubclass_(val, bias.Bias):
            raise ValueError("bias_model must be a subclass of bias.Bias")
        return val

    @parameter
    def halo_exclusion(self, val):
        """A string identifier for the type of halo exclusion used (or None)"""
        if val is None:
            val = "None"
        available = ["None", "sphere", "ellipsoid", "ng_matched", 'schneider']
        if val not in available:
            raise ValueError("halo_exclusion not acceptable: " + str(val) + " " + str(type(val)))
        else:
            return val

    @parameter
    def cm_params(self, val):
        return val

    @parameter
    def rmin(self, val):
        return val

    @parameter
    def rmax(self, val):
        return val

    @parameter
    def rnum(self, val):
        return val

    @parameter
    def scale_dependent_bias(self, val):
        try:
            if val:
                return True
            else:
                return False
        except:
            raise ValueError("scale_dependent_bias must be a boolean/have logical value")
#===============================================================================
# Start the actual calculations
#===============================================================================
    @cached_property("rmin", "rmax", "rnum")
    def r(self):
        if type(self.rmin) == list or type(self.rmin) == np.ndarray:
            r = np.array(self.rmin)
        else:
            if self.rlog:
                r = np.exp(np.linspace(np.log(self.rmin), np.log(self.rmax), self.rnum))
            else:
                r = np.linspace(self.rmin, self.rmax, self.rnum)

        return r * u.Mpc / self._hunit

    @cached_property("hod_model", "hod_params")
    def hod(self):
        if issubclass_(self.hod_model, hod.HOD):
            return self.hod_model(**self.hod_params)
        else:
            return get_model(self.hod_model, "halomod.hod", **self.hod_params)

    @cached_property("hod", "dlog10m")
    def M(self):
        return 10 ** np.arange(self.hod.mmin, 18, self.dlog10m) * u.MsolMass / self._hunit

    @cached_property("hod", "M")
    def n_sat(self):
        """Average satellite occupancy of halo of mass M"""
        return self.hod.ns(self.M)

    @cached_property("hod", "M")
    def n_cen(self):
        """Average satellite occupancy of halo of mass M"""
        return self.hod.nc(self.M)

    @cached_property("hod", "M")
    def n_tot(self):
        """Average satellite occupancy of halo of mass M"""
        return self.hod.ntot(self.M)

    @cached_property("bias_model", "nu", "delta_c", "delta_halo", "n", "bias_params")
    def bias(self):
        """A class containing the elements necessary to calculate the halo bias"""
        if issubclass_(self.bias_model, bias.Bias):
            return self.bias_model(nu=self.nu, delta_c=self.delta_c,
                                   delta_halo=self.delta_halo, n=self.n,
                                   **self.bias_params).bias()
        else:
            return get_model(self.bias_model, "halomod.bias",
                             nu=self.nu, delta_c=self.delta_c,
                             delta_halo=self.delta_halo, n=self.n,
                             **self.bias_params).bias()

    @cached_property("cm_relation", "nu", "z", "growth_model", "M", "cm_params")
    def cm(self):
        """A class containing the elements necessary to calculate the concentration-mass relation"""
        if issubclass_(self.cm_relation, CMRelation):
            return self.cm_relation(nu=self.nu, z=self.z, growth=self._growth,
                                    M=self.M, **self.cm_params)
        else:
            return get_model(self.cm_relation, "halomod.concentration",
                           nu=self.nu, z=self.z, growth=self._growth,
                           M=self.M, **self.cm_params)

    @cached_property("cm","M")
    def concentration(self):
        """
        The concentrations corresponding to `.M`
        """
        return self.cm.cm(self.M)

    @cached_property("halo_profile", "delta_halo", "cm_relation", "z", "mean_density0")
    def profile(self):
        """A class containing the elements necessary to calculate halo profile quantities"""
        if issubclass_(self.halo_profile, profiles.Profile):
            return self.halo_profile(cm_relation=self.cm,
                                     mean_dens=self.mean_density0,
                                     delta_halo=self.delta_halo, z=self.z)
        else:
            return get_model(self.halo_profile, "halomod.profiles",
                             cm_relation=self.cm,
                             mean_dens=self.mean_density0,
                             delta_halo=self.delta_halo, z=self.z)

    @cached_property("dndm", "n_tot")
    def n_gal(self):
        """
        The total number density of galaxies in halos of mass M
        """
        return self.dndm * self.n_tot

    @cached_property("M", "dndm", "n_tot", "ng")
    def mean_gal_den(self):
        """
        The mean number density of galaxies
        """
        if self.ng is not None:
            return self.ng * self.M.unit * self.dndm.unit
        else:
#             Integrand is just the density of galaxies at mass M
            integrand = self.M * self.dndm * self.n_tot
        return intg.trapz(integrand, dx=np.log(self.M[1] / self.M[0]))


    @cached_property("M", "dndm", "n_tot", "bias")
    def bias_effective(self):
        """
        The galaxy number weighted halo bias factor (Tinker 2005)
        """
        # Integrand is just the density of galaxies at mass M by bias
        integrand = self.M * self.dndm * self.n_tot * self.bias
        b = intg.trapz(integrand, dx=np.log(self.M[1] / self.M[0]))
        return b / self.mean_gal_den

    @cached_property("M", 'dndm', 'n_tot', "mean_gal_den")
    def mass_effective(self):
        """
        Average group halo mass, or host-halo mass (in log10 units)
        """
        # Integrand is just the density of galaxies at mass M by M
        integrand = self.M ** 2 * self.dndm * self.n_tot

        m = intg.trapz(integrand, dx=np.log(self.M[1] / self.M[0]))
        return np.log10((m / self.mean_gal_den).value)

    @cached_property("M", "dndm", "n_sat", "mean_gal_den")
    def satellite_fraction(self):
        # Integrand is just the density of satellite galaxies at mass M
        integrand = self.M * self.dndm * self.n_sat
        s = intg.trapz(integrand, dx=np.log(self.M[1] / self.M[0]))
        return s / self.mean_gal_den

    @cached_property("satellite_fraction")
    def central_fraction(self):
        return 1 - self.satellite_fraction

    @cached_property("nonlinear", "power", "nonlinear_power")
    def matter_power(self):
        """The matter power used in calculations -- can be linear or nonlinear

        .. note :: Linear power is available through :attr:`.power`
        """
        if self.nonlinear:
            return self.nonlinear_power
        else:
            return self.power

    @cached_property("matter_power", 'k', 'r')
    def dm_corr(self):
        """
        The dark-matter-only two-point correlation function of the given cosmology
        """
        return tools.power_to_corr_ogata(self.matter_power,self.k, self.r)

    @cached_property("k", "M", "dndm", "n_sat", "n_cen", 'hod', 'profile', "mean_gal_den")
    def _power_gg_1h_ss(self):
        """
        The sat-sat part of the 1-halo term of the galaxy power spectrum
        """
        if USEFORT:
            ## The fortran routine is very very slightly faster. SHould remove it.
            u = self.profile.u(self.k, self.M, norm='m')
            p = fort.power_gal_1h_ss(nlnk=len(self.k),
                                     nm=len(self.M),
                                     u=np.asfortranarray(u),
                                     dndm=self.dndm.value,
                                     nsat=self.n_sat,
                                     ncen=self.n_cen,
                                     mass=self.M.value,
                                     central=self.hod._central)
        else:
            u = self.profile.u(self.k, self.M, norm='m')
            integ = u**2 * self.dndm * self.M * self.n_sat**2
            if self.hod._central:
                integ *= self.n_cen

            p = intg.trapz(integ,dx=self.dlog10m*np.log(10))

        return p / self.mean_gal_den ** 2

    @cached_property("profile","dndm","M","k","mean_density","delta_halo","dlog10m")
    def _power_mm_1h(self):
        """
        The halo model-derived nonlinear 1-halo matter power
        """
        u = self.profile.u(self.k, self.M, norm="m")
        integrand = self.dndm * self.M ** 3 * u**2

        ### The following may not need to be done?
        r = np.pi/self.k # half the radius
        mmin = 4*np.pi * r**3 * self.mean_density * self.delta_halo/3
        mask = np.repeat(self.M,len(self.k)).reshape(len(self.M),len(self.k)) < mmin
        integrand[mask.T] = 0

        return intg.trapz(integrand,dx=np.log(10)*self.dlog10m)/self.mean_density**2

    @cached_property("profile","dndm","M","r","mean_density","delta_halo","dlog10m")
    def _corr_mm_1h(self):
        """
        The halo model-derived nonlinear 1-halo matter power
        """
        if self.profile.has_lam:
            lam = self.profile.lam(self.r, self.M, norm="m")
            integrand = self.dndm * self.M ** 3 * lam

            ### The following may not need to be done?
            r = self.r/2 # half the radius
            mmin = 4*np.pi * r**3 * self.mean_density * self.delta_halo/3
            mask = np.repeat(self.M,len(self.r)).reshape(len(self.M),len(self.r)) < mmin
            integrand[mask.T] = 0
            return intg.trapz(integrand,dx=np.log(10)*self.dlog10m)/self.mean_density**2
        else:
            return tools.power_to_corr_ogata(self._power_mm_1h,self.k,self.r)


    @cached_property("_power_gal_1h_ss", "k", "r")
    def _corr_gg_1h_ss(self):
        return tools.power_to_corr_ogata(self._power_gg_1h_ss,
                                         self.k, self.r)

    @cached_property("r", "M", "dndm", "n_cen", "n_sat", "mean_density0", "delta_halo", "mean_gal_den")
    def _corr_gg_1h_cs(self):
        """The cen-sat part of the 1-halo galaxy correlations"""
        rho = self.profile.rho(self.r, self.M, norm="m")
        if USEFORT:
            c = fort.corr_gal_1h_cs(nr=len(self.r),
                                    nm=len(self.M),
                                    r=self.r.value,
                                    mass=self.M.value,
                                    dndm=self.dndm.value,
                                    ncen=self.n_cen,
                                    nsat=self.n_sat,
                                    rho=np.asfortranarray(rho),
                                    mean_dens=self.mean_density0.value,
                                    delta_halo=self.delta_halo) * self.mean_gal_den.unit ** 2
        else:
            ## The following is 10 times worse, but it's a small fraction of the total budget.
            mmin = 4*np.pi * self.r**3 * self.mean_density * self.delta_halo/3
            mask = np.repeat(self.M,len(self.r)).reshape(len(self.M),len(self.r)) < mmin
            integ = self.dndm * 2 * self.n_cen * self.n_sat * rho * self.M
            integ[mask.T] = 0
            c = intg.trapz(integ,dx=self.dlog10m*np.log(10))

        return c / self.mean_gal_den ** 2


    @cached_property("r", "M", "dndm", "n_cen", "n_sat", "hod", "mean_density0", "delta_halo",
                     "mean_gal_den", "_corr_gal_1h_cs", "_corr_gal_1h_ss")
    def corr_gal_1h(self):
        """The 1-halo term of the galaxy correlations"""
        if self.profile.has_lam:
            rho = self.profile.rho(self.r, self.M, norm="m")
            lam = self.profile.lam(self.r, self.M, norm="m")
            if USEFORT:
                ## Using fortran only saves about 15% of time on this single routine (eg. 7ms --> 8.7ms)
                c = fort.corr_gal_1h(nr=len(self.r),
                                     nm=len(self.M),
                                     r=self.r.value,
                                     mass=self.M.value,
                                     dndm=self.dndm.value,
                                     ncen=self.n_cen,
                                     nsat=self.n_sat,
                                     rho=np.asfortranarray(rho),
                                     lam=np.asfortranarray(lam),
                                     central=self.hod._central,
                                     mean_dens=self.mean_density0.value,
                                     delta_halo=self.delta_halo) * self.mean_gal_den.unit ** 2
            else:
                integ = self.dndm * self.n_sat**2 * lam
                if self.hod._central:
                    integ *= self.n_cen

                mmin = 4*np.pi*self.r**3*self.mean_density*self.delta_halo/3
                mask = np.repeat(self.M,len(self.r)).reshape(len(self.M),len(self.r)) > mmin
                integ2 = h.dndm*2 * h.n_cen*h.n_sat*rho
                integ[mask.T] += integ2[mask.T]

                integ *= self.M

                c = intg.trapz(integ,dx=self.dlog10m*np.log(10))


            return c / self.mean_gal_den ** 2

        else:
            return self._corr_gal_1h_cs + self._corr_gal_1h_ss

    @cached_property("profile", "k", "M", "halo_exclusion", "scale_dependent_bias",
                     "bias", "n_tot", 'dndm', "matter_power", "r", "dm_corr",
                     "mean_gal_den", "delta_halo", "mean_density0")
    def corr_gal_2h(self):
        """The 2-halo term of the galaxy correlation"""
        u = self.profile.u(self.k, self.M , norm='m')
        corr_2h = thalo(self.halo_exclusion, self.scale_dependent_bias,
                        self.M.value, self.bias, self.n_tot,
                        self.dndm.value, np.log(self.k.value),
                        self.matter_power.value, u, self.r.value, self.dm_corr,
                        self.mean_gal_den.value, self.delta_halo,
                        self.mean_density0.value, self.nthreads_2halo)
        return corr_2h

    @cached_property("corr_gal_1h", "corr_gal_2h")
    def  corr_gal(self):
        """The galaxy correlation function"""
        return self.corr_gal_1h + self.corr_gal_2h

    def _find_m_min(self, ng):
        """
        Calculate the minimum mass of a halo to contain a (central) galaxy
        based on a known mean galaxy density
        """

        self.power  # This just makes sure the power is gotten and copied
        c = deepcopy(self)
        c.update(hod_params={"M_min":8}, dlog10m=0.01)

        integrand = c.M * c.dndm * c.n_tot

        if self.hod.sharp_cut:
            integral = intg.cumtrapz(integrand[::-1], dx=np.log(c.M[1] / c.M[0]))

            if integral[-1] < ng:
                raise NGException("Maximum mean galaxy density exceeded: " + str(integral[-1]))

            ind = np.where(integral > ng)[0][0]

            m = c.M[::-1][1:][max(ind - 4, 0):min(ind + 4, len(c.M))]
            integral = integral[max(ind - 4, 0):min(ind + 4, len(c.M))]


            spline_int = spline(np.log(integral), np.log(m.value), k=3)
            mmin = spline_int(np.log(ng)) / np.log(10)
        else:
            # Anything else requires us to do some optimization unfortunately.
            integral = intg.simps(integrand, dx=np.log(c.M[1] / c.M[0]))
            if integral < ng:
                raise NGException("Maximum mean galaxy density exceeded: " + str(integral))

            def model(mmin):
                c.update(hod_params={"M_min":mmin})
                integrand = c.M * c.dndm * c.n_tot
                integral = intg.simps(integrand, dx=np.log(c.M[1] / c.M[0]))
                return abs(integral - ng)

            res = minimize(model, 12.0, tol=1e-3,
                           method="Nelder-Mead", options={"maxiter":200})
            mmin = res.x[0]

        return mmin


class NGException(Exception):
    pass
