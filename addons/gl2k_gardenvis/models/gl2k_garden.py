# -*- coding: utf-'8' "-*-"

from openerp import models, fields, api, tools, registry
from openerp.addons.fso_base.tools.image import resize_to_thumbnail
from openerp.tools.image import image_resize_image
from openerp.tools.translate import _

from openerp.http import request

import uuid
import datetime

import logging
logger = logging.getLogger(__name__)


class GL2KGarden(models.Model):
    _name = "gl2k.garden"
    #_inherit = 'mail.thread'

    # Fields to watch for geo localization
    _geo_location_fields = ('zip', 'country_id', 'city')

    # Fields to watch for refreshing the materialized views
    _refresh_matviews_fields = ('zip', 'street', 'garden_size', 'garden_image_file', 'state')

    # States to sum for the visualization (materialized views)
    _valid_states = ('new', 'approved')

    def init(self, cr):
        # MATERIALIZED VIEW (BUNDESLAND)
        cr.execute("SELECT * FROM pg_class WHERE relkind = 'm' and relname = 'garden_rep_state';")
        if cr.fetchone():
            cr.execute("DROP MATERIALIZED VIEW garden_rep_state;")
        state_view = """
                    CREATE materialized VIEW garden_rep_state
                    AS        
                        SELECT 
                             g2kg.cmp_state_id
                            ,res_country_state.name AS cmp_state
                            ,sum(g2kg.garden_size) AS garden_size
                            ,sum(g2kg.garden_size)/
                                (select sum(sq3.garden_size) 
                                 from gl2k_garden sq3
                                 where sq3.state in %s
                                   and sq3.cmp_state_id IS NOT NULL) AS garden_size_peg
                            ,json_agg(g2kg.id) AS record_ids
                            ,json_agg(DISTINCT g2kg.zip) AS zip_list
                            ,(
                              select json_agg(sq2.id) 
                              from (															   
                                    select sq1.id,  sq1.cmp_state_id
                                    from gl2k_garden sq1 
                                    where sq1.garden_image_file IS NOT NULL 
                                      and sq1.state in %s) sq2 
                              where sq2.cmp_state_id = g2kg.cmp_state_id
                             ) thumbnail_record_ids
                        from gl2k_garden g2kg
                        left join res_country_state 
                            on g2kg.cmp_state_id = res_country_state.id
                        where g2kg.cmp_state_id is not null 
                          and g2kg.state in %s
                        group by 
                             g2kg.cmp_state_id
                            ,res_country_state.name        
        """ % (str(self._valid_states), str(self._valid_states), str(self._valid_states))
        cr.execute(state_view)

        # MATERIALIZED VIEW (GEMEINDE)
        cr.execute("SELECT * FROM pg_class WHERE relkind = 'm' and relname = 'garden_rep_community';")
        if cr.fetchone():
            cr.execute("DROP MATERIALIZED VIEW garden_rep_community;")
        community_view = """
                        CREATE materialized VIEW garden_rep_community
                        AS
                            select
                                 g2kg.cmp_community_code
                                ,g2kg.cmp_community
                                ,sum(g2kg.garden_size) AS garden_size
                                ,sum(g2kg.garden_size)/
                                 (
                                     select
                                         sum(sq3.garden_size)
                                     from gl2k_garden sq3
                                     where sq3.cmp_community is not null
                                       and sq3.state in %s
                                 ) AS garden_size_peg
                                ,json_agg(g2kg.id) AS record_ids
                                ,(
                                  select
                                    json_agg(sq2.id)
                                  from
                                    (
                                    select
                                         sq1.id
                                        ,sq1.cmp_community_code
                                    from gl2k_garden sq1
                                    where sq1.cmp_community is not null
                                      and sq1.state in %s
                                      and sq1.garden_image_file is not null
                                    ) sq2
                                   where sq2.cmp_community_code = g2kg.cmp_community_code
                                    
                                 ) thumbnail_record_ids
                                ,json_agg(DISTINCT g2kg.zip) AS zip_list
                                ,(
                                  select
                                    json_agg(distinct sq2.cmp_latitude)
                                  from
                                    (
                                    select
                                         sq1.cmp_latitude
                                        ,sq1.cmp_community_code
                                    from gl2k_garden sq1
                                    where sq1.cmp_community is not null
                                      and sq1.state in %s
                                      and sq1.cmp_latitude is not null
                                    ) sq2
                                   where sq2.cmp_community_code = g2kg.cmp_community_code
                                    
                                 ) latitude
                                ,(
                                  select
                                    json_agg(distinct sq2.cmp_longitude)
                                  from
                                    (
                                    select
                                         sq1.cmp_longitude
                                        ,sq1.cmp_community_code
                                    from gl2k_garden sq1
                                    where sq1.cmp_community is not null
                                      and sq1.state in %s
                                      and sq1.cmp_longitude is not null
                                    ) sq2
                                   where sq2.cmp_community_code = g2kg.cmp_community_code
                                    
                                 ) longitude
                            from gl2k_garden g2kg
                            where g2kg.cmp_community is not null
                              and g2kg.state in %s
                            group by 
                                 g2kg.cmp_community_code
                                ,g2kg.cmp_community        
        """ % (str(self._valid_states), str(self._valid_states), str(self._valid_states), str(self._valid_states),
               str(self._valid_states))
        cr.execute(community_view)

    # ------
    # FIELDS
    # ------
    def _default_country(self):
        austria = self.env['res.country'].search([('code', '=', 'AT')], limit=1)
        return austria or False

    state = fields.Selection(string="State", selection=[('new', 'New'),
                                                        ('approved', 'Approved'),
                                                        ('rejected', 'Rejected'),
                                                        ('invalid', 'Invalid'),
                                                        ('disabled', 'Disabled')],
                             default="new", index=True, track_visibility='onchange')

    # Form input fields
    # -----------------
    # NEW: Requested by Gerald
    type = fields.Selection(string="Typ", selection=[('privat', 'Privat'),
                                                     ('gemeinde', 'Gemeinde'),
                                                     ('schule', 'Schule'),
                                                     ('verein', 'Andere')],
                            track_visibility='onchange')
    organisation_name = fields.Char(string="Organisationsname")
    #
    email = fields.Char(string="E-Mail", required=True, track_visibility='onchange')
    # ATTENTION: This is NOT! transfered to the res.partner in FS-Online but done by FRST workflow!
    newsletter = fields.Boolean(string="Newsletter", help="Subscribe for the Newsletter")

    salutation = fields.Char(string="Salutation")
    firstname = fields.Char(string="Firstname", track_visibility='onchange')
    lastname = fields.Char(string="Lastname", required=True, track_visibility='onchange')

    # res.partner address
    zip = fields.Char(string="Zip", required=True, index=True, track_visibility='onchange')
    street = fields.Char(string="Street", track_visibility='onchange')
    street_number_web = fields.Char(string="Street Number Web", track_visibility='onchange')
    city = fields.Char(string="City", track_visibility='onchange')
    country_id = fields.Many2one(string="Country", comodel_name="res.country", required=True,
                                 default=_default_country, domain="[('code', '=', 'AT')]",
                                 track_visibility='onchange')

    # garden fields
    garden_size = fields.Float(string="Garden Size m2", required=True, track_visibility='onchange')
    garden_image_name = fields.Char(string="Garden Image Name", track_visibility='onchange')
    garden_image_write_date = fields.Datetime(string="Bild geandert am", readonly=True)
    garden_image_file = fields.Binary(string="Garden Image File")

    # Computed and system fields (non user land)
    # ------------------------------------------
    cmp_image_file = fields.Binary(string="Computed Garden Image", readonly=True,
                                   compute="compute_images", store=True)
    cmp_thumbnail_file = fields.Binary(string="Computed Garden Image Thumbnail", readonly=True,
                                       compute="compute_images", store=True)

    # Will be computed based on zip field by the help of the addon base_location
    cmp_better_zip_id = fields.Many2one(string="Better Zip", comodel_name="res.better.zip", readonly=True, 
                                        index=True, ondelete="set null")
    cmp_state_id = fields.Many2one(string="Computed State", comodel_name="res.country.state", readonly=True,
                                   index=True, ondelete="set null")
    cmp_county_province = fields.Char(string="Computed Province", readonly=True)
    cmp_county_province_code = fields.Char(string="Computed Province Code", readonly=True)
    cmp_community = fields.Char(string="Computed Community", readonly=True, index=True)
    cmp_community_code = fields.Char(string="Computed Community Code", readonly=True, index=True)
    cmp_city = fields.Char(string="Computed City", readonly=True)

    cmp_latitude = fields.Char("Latitude", help="estimated latitude (wgs84)", readonly=True)
    cmp_longitude = fields.Char("Longitude", help="estimated longitude (wgs84)", readonly=True)

    # Login (token/fstoken) information
    # ---------------------------------
    login_token_used = fields.Char("Login Token", readonly=True)
    # TODO: login_token_id = fields.Many2one() and other token info like CDS, Action or Origin

    # E-Mail validation / Double-Opt-In
    # ---------------------------------
    email_validate = fields.Char(string="E-Mail to validate", readonly=True, track_visibility='onchange')
    email_validate_token = fields.Char(string="E-Mail validation token", readonly=True,
                                       help="To be used in the Double-Opt-In link")
    # If this is set the link was klicked
    email_validated_at = fields.Datetime(string="E-Mail validated", readonly=True)

    # Created / Linked res.partner
    # ----------------------------
    partner_id = fields.Many2one(string="Partner", comodel_name="res.partner", readonly=True,
                                 track_visibility='onchange')

    # ONCHANGE
    # --------
    # Update fields based on zip field
    # HINT: This will not be stored by the gui since all fields are read only therefore it is added to create and write
    #       Still this is is left here because it is very convenient to see the cmp field values directly
    @api.onchange(*_geo_location_fields)
    def onchange_zip(self):
        for r in self:
            cmp_fields_vals = self.get_cmp_fields_vals(zip=r.zip, country_id=r.country_id.id, city=r.city)

            for v in cmp_fields_vals:
                r[v] = cmp_fields_vals[v]

    # COMPUTED
    # --------
    @api.depends('garden_image_file')
    def compute_images(self):
        for r in self:
            if r.garden_image_file:
                r.cmp_image_file = image_resize_image(r.garden_image_file, size=(1200, None), avoid_if_small=True)
                r.cmp_thumbnail_file = resize_to_thumbnail(img=r.garden_image_file, box=(300, 300))
            else:
                r.cmp_image_file = False
                r.cmp_thumbnail_file = False

    # -------
    # METHODS
    # -------
    @api.model
    def get_cmp_fields_vals(self, zip='', country_id='', city=''):
        better_zip = False
        better_zip_obj = self.env['res.better.zip'].sudo()
        if zip and country_id and city:
            better_zip = better_zip_obj.search([('name', '=', zip),
                                                ('country_id', '=', country_id),
                                                ('city', '=', city)
                                                ], limit=1)
            if not better_zip:
                better_zip = better_zip_obj.search([('name', '=', zip),
                                                    ('country_id', '=', country_id),
                                                    ('city', 'ilike', city)
                                                    ], limit=1)
        if not better_zip and zip and country_id:
            better_zip = better_zip_obj.search([('name', '=', zip),
                                                ('country_id', '=', country_id),
                                                ], limit=1)

        return {
            'cmp_better_zip_id': better_zip.id if better_zip else False,
            'cmp_state_id': better_zip.state_id.id if better_zip and better_zip.state_id else False,
            'cmp_city': better_zip.city if better_zip else False,
            'cmp_county_province': better_zip.county_province if better_zip else False,
            'cmp_county_province_code': better_zip.county_province_code if better_zip else False,
            'cmp_community': better_zip.community if better_zip else False,
            'cmp_community_code': better_zip.community_code if better_zip else False,
            #
            'cmp_latitude': better_zip.latitude if better_zip else False,
            'cmp_longitude': better_zip.longitude if better_zip else False,
        }

    @api.model
    def refresh_materialized_views(self):
        logger.info("REFRESH MATERIALIZED VIEWS")
        cr = self.env.cr
        cr.execute("REFRESH MATERIALIZED VIEW garden_rep_state; REFRESH MATERIALIZED VIEW garden_rep_community;")

    @api.multi
    def create_update_partner(self):
        for r in self:

            # Only update a partner if a user is logged in for the current web request!
            # ATTENTION: This prevents partner data (especially after a partner merge from frst) to be changed by 
            #            not logged in users (by the public website user)
            # TODO: ATTENTION: I am not sure if the request we get here is always the current request of the
            #                  web controller - I need to thest this especially for multi threaded operations!
            logged_in = request and request.uid and request.website and request.uid != request.website.user_id.id
            if r.partner_id and not logged_in:
                logger.warning('Update of linked Partner is only allowed if the user is logged in! '
                               'Partner with ID %s exists already and no user is logged in! Skipping Partner update!'
                               '' % r.partner_id.id)
                continue

            # Partner values
            # ATTENTION: newsletter will not be transfered!
            partner_vals = {
                # TODO: salutation
                'email': r.email,
                'firstname': r.firstname,
                'lastname': r.lastname,
                'zip': r.zip,
                'street': r.street,
                'street_number_web': r.street_number_web,
                'city': r.city,
                'country_id': r.country_id.id if r.country_id else False,
            }
            # Update partner
            if r.partner_id:
                r.partner_id.sudo().write(partner_vals)
            # Create partner
            # ATTENTION: If we just created a garden record and a user is logged in we still create a new partner
            #            This may be just what we want because the "Dublettenzusammenlegung" of FRST may link the new
            #            partner with the existing one if the values match enough.
            else:
                partner_obj_su = self.env['res.partner'].sudo()
                partner = partner_obj_su.create(partner_vals)
                r.write({'partner_id': partner.id})

    @api.multi
    def create_update_email_validation(self):
        for r in self:
            # HINT: No E-Mail should not happen since it is a mandatory field!
            if r.email != r.email_validate:
                r.write({
                    'email_validate': r.email,
                    'email_validate_token': str(uuid.uuid1()),
                    'email_validated_at': False
                })

    # ----
    # CRUD
    # ----
    @api.model
    def create(self, vals):
        # Add "image changed" date
        if 'garden_image_file' in vals and 'garden_image_write_date' not in vals:
            vals['garden_image_write_date'] = datetime.datetime.now()

        # Make sure the country is Austria or ignore the garden record by setting its state to 'invalid'
        country_id = vals.get('country_id', False)
        # Check for default country
        if country_id and self._default_country() and country_id != self._default_country().id:
            logger.error("Country must be Austria!")
            vals['state'] = 'invalid'

        # Append computed fields values
        if vals.get('zip', False):
            vals.update(self.get_cmp_fields_vals(zip=vals.get('zip'),
                                                 country_id=vals.get('country_id', self._default_country().id),
                                                 city=vals.get('city', '')))

        # Check if this e-mail is not already used
        # HINT: This will only be checked on record create and will ALSO be checked by the web controller method
        #       FsoFormsGL2KGardenVis -> validate_fields()!
        if vals.get('email', False):
            if self.search([('email', '=', vals['email'])], limit=1):
                logger.error('Record for email %s exists already!' % vals['email'])
                return False

        # Create the record
        record = super(GL2KGarden, self).create(vals)

        # Create a res.partner
        if 'partner_id' not in vals:
            record.create_update_partner()

        # Create or update email_validate fields
        # ATTENTION: DISABLED BECAUSE WE DECIDED EMAILS ARE DONE BY FRST
        # if 'email' in vals:
        #     record.create_update_email_validation()

        # Update materialized views
        record.refresh_materialized_views()

        return record

    @api.multi
    def write(self, vals):
        # Add "image changed" date
        if 'garden_image_file' in vals and 'garden_image_write_date' not in vals:
            vals['garden_image_write_date'] = datetime.datetime.now()

        # Update self first
        # HINT: res is just a boolean
        res = super(GL2KGarden, self).write(vals)

        # Update computed fields
        if res and any(f in vals for f in self._geo_location_fields):
            for r in self:
                cmp_vals = {}
                # Check for default country
                if r.country_id and self._default_country() and r.country_id.id != self._default_country().id:
                    logger.error("Country must be Austria (ID %s)" % r.id)
                    cmp_vals['state'] = 'invalid'
                # Get computed field values
                cmp_vals.update(r.get_cmp_fields_vals(zip=r.zip, country_id=r.country_id.id, city=r.city))
                # Update record
                r.write(cmp_vals)

        # Update the res.partner if logged in
        # ATTENTION: Only allow updates to the partner if a user is logged in! This prevents partner data (especially
        #            after a partner merge in frst) to be changed by the public website user!
        if 'partner_id' not in vals:
            self.create_update_partner()

        # Create or update email_validate fields
        # ATTENTION: DISABLED BECAUSE WE DECIDED EMAILS ARE DONE BY FRST
        #if 'email' in vals:
        #    self.create_update_email_validation()

        # Update materialized views
        if any(f in vals for f in self._refresh_matviews_fields):
            self.refresh_materialized_views()

        return res
